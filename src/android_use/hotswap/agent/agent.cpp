// JVMTI hot swap agent. Runs inside a debuggable app via:
//   am attach-agent <process> <this.so>=<plan file>
//
// Plan file, one directive per line:
//   refresh auto|recreate|none
//   compose <group key>               (auto: composable groups whose slots must be reset)
//   helper <dex>                      (hotswap.agent.HotswapState, for rebuilding activities)
//   class <dotted.class.Name> <dex file holding exactly that class>
//   resources <resource APK>          (replaces the app's resource table, Android 11+)
//   statics <dotted.class.Name> <dex> (also copy that class's static field values)
//
// Each class is redefined in place when the app already has it (loading it first if
// needed) and defined in the app class loader when it is new. The verdict goes to
// logcat (tag "hotswap") and to "<plan file>.result".
#include <android/log.h>
#include <jni.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "jvmti.h"

#define LOG(...) __android_log_print(ANDROID_LOG_INFO, "hotswap", __VA_ARGS__)

namespace {

struct Entry {
    std::string name, dex_path;
    std::vector<unsigned char> dex;
    jclass klass = nullptr;
};

struct Extensions {
    jvmtiExtensionFunction add_dex_file = nullptr;
    jvmtiExtensionFunction structural_redefine = nullptr;
    jvmtiExtensionFunction last_error = nullptr;
};

bool read_file(const std::string& path, std::vector<unsigned char>* out) {
    FILE* f = fopen(path.c_str(), "rb");
    if (!f) return false;
    unsigned char buf[8192];
    size_t n;
    while ((n = fread(buf, 1, sizeof buf, f)) > 0) out->insert(out->end(), buf, buf + n);
    fclose(f);
    return !out->empty();
}

jint report(const std::string& plan, bool ok, const std::string& text) {
    std::string line = (ok ? "OK " : "FAIL ") + text;
    LOG("%s", line.c_str());
    if (FILE* f = fopen((plan + ".result").c_str(), "w")) {
        fputs(line.c_str(), f);
        fputc('\n', f);
        fclose(f);
    }
    return ok ? JNI_OK : JNI_ERR;
}

std::string error_text(jvmtiEnv* jvmti, const Extensions& ext, jvmtiError err) {
    char* name = nullptr;
    jvmti->GetErrorName(err, &name);
    std::string text = name ? name : std::to_string(err);
    char* detail = nullptr;
    if (ext.last_error && ext.last_error(jvmti, &detail) == JVMTI_ERROR_NONE && detail) {
        text += ": ";
        text += detail;
        text = text.substr(0, text.find('\n'));  // drop the Java stack trace
    }
    return text;
}

Extensions find_extensions(jvmtiEnv* jvmti) {
    Extensions ext;
    jint count = 0;
    jvmtiExtensionFunctionInfo* infos = nullptr;
    if (jvmti->GetExtensionFunctions(&count, &infos) != JVMTI_ERROR_NONE) return ext;
    for (jint i = 0; i < count; i++) {
        std::string id = infos[i].id;
        if (id == "com.android.art.classloader.add_to_dex_class_loader")
            ext.add_dex_file = infos[i].func;
        else if (id == "com.android.art.class.structurally_redefine_classes")
            ext.structural_redefine = infos[i].func;
        else if (id == "com.android.art.misc.get_last_error_message")
            ext.last_error = infos[i].func;
    }
    return ext;
}

bool on_main_thread(JNIEnv* env) {
    jclass looper = env->FindClass("android/os/Looper");
    jobject mine = env->CallStaticObjectMethod(
        looper, env->GetStaticMethodID(looper, "myLooper", "()Landroid/os/Looper;"));
    jobject main = env->CallStaticObjectMethod(
        looper, env->GetStaticMethodID(looper, "getMainLooper", "()Landroid/os/Looper;"));
    return mine && env->IsSameObject(mine, main);
}

// The app's own class loader: the main thread's context loader, else the loader of
// any loaded Activity subclass.
jobject app_class_loader(JNIEnv* env, jvmtiEnv* jvmti, jint count, jclass* classes) {
    jclass thread = env->FindClass("java/lang/Thread");
    jobject current = env->CallStaticObjectMethod(
        thread, env->GetStaticMethodID(thread, "currentThread", "()Ljava/lang/Thread;"));
    jobject loader = env->CallObjectMethod(
        current, env->GetMethodID(thread, "getContextClassLoader", "()Ljava/lang/ClassLoader;"));
    if (env->ExceptionCheck()) env->ExceptionClear();
    if (loader && on_main_thread(env)) return loader;
    jclass activity = env->FindClass("android/app/Activity");
    for (jint i = 0; i < count; i++) {
        if (!env->IsAssignableFrom(classes[i], activity)) continue;
        jobject found = nullptr;
        if (jvmti->GetClassLoader(classes[i], &found) == JVMTI_ERROR_NONE && found) return found;
    }
    return loader;
}

jclass load_class(JNIEnv* env, jobject loader, const std::string& name) {
    jclass cl = env->FindClass("java/lang/ClassLoader");
    jmethodID load = env->GetMethodID(cl, "loadClass", "(Ljava/lang/String;)Ljava/lang/Class;");
    jobject found = env->CallObjectMethod(loader, load, env->NewStringUTF(name.c_str()));
    if (env->ExceptionCheck()) {
        env->ExceptionClear();
        return nullptr;
    }
    return static_cast<jclass>(found);
}

jint JNICALL tag_instance(jlong, jlong, jlong* tag, jint, void* wanted) {
    *tag = *static_cast<jlong*>(wanted);
    return JVMTI_VISIT_OBJECTS;
}

// Every Activity instance that is still alive, found by walking the heap.
std::vector<jobject> live_activities(JNIEnv* env, jvmtiEnv* jvmti) {
    std::vector<jobject> result;
    jint count = 0;
    jclass* classes = nullptr;
    if (jvmti->GetLoadedClasses(&count, &classes) != JVMTI_ERROR_NONE) return result;
    jclass activity = env->FindClass("android/app/Activity");
    jlong tag = 0x686f7473776170;  // "hotswap"
    jvmtiHeapCallbacks callbacks;
    memset(&callbacks, 0, sizeof callbacks);
    callbacks.heap_iteration_callback = tag_instance;
    // The class filter matches exact classes only, so walk each Activity subclass.
    for (jint i = 0; i < count; i++)
        if (env->IsAssignableFrom(classes[i], activity))
            jvmti->IterateThroughHeap(0, classes[i], &callbacks, &tag);
    jint found = 0;
    jobject* objects = nullptr;
    if (jvmti->GetObjectsWithTags(1, &tag, &found, &objects, nullptr) != JVMTI_ERROR_NONE)
        return result;
    jmethodID finishing = env->GetMethodID(activity, "isFinishing", "()Z");
    jmethodID destroyed = env->GetMethodID(activity, "isDestroyed", "()Z");
    for (jint i = 0; i < found; i++) {
        jvmti->SetTag(objects[i], 0);
        if (env->CallBooleanMethod(objects[i], finishing)) continue;
        if (env->CallBooleanMethod(objects[i], destroyed)) continue;
        result.push_back(objects[i]);
    }
    return result;
}

// Rebuild the activities so the UI is drawn by the new code. Activity fields are lost
// unless saved; statics, singletons and ViewModels survive.
int recreate_activities(JNIEnv* env, const std::vector<jobject>& activities, jclass helper) {
    jclass activity = env->FindClass("android/app/Activity");
    jmethodID recreate = env->GetMethodID(activity, "recreate", "()V");
    // The helper rebuilds the activity too, and afterwards puts back scroll offsets and
    // other view state the platform loses.
    jmethodID keeping = helper ? env->GetStaticMethodID(helper, "recreate", "(Landroid/app/Activity;)V")
                               : nullptr;
    if (env->ExceptionCheck()) {
        env->ExceptionClear();
        keeping = nullptr;
    }
    int done = 0;
    for (jobject one : activities) {
        if (keeping) env->CallStaticVoidMethod(helper, keeping, one);
        else env->CallVoidMethod(one, recreate);
        if (env->ExceptionCheck()) env->ExceptionClear();
        else done++;
    }
    return done;
}

// True (with the exception cleared and described) when the last JNI call threw.
bool threw(JNIEnv* env, std::string* error) {
    if (!env->ExceptionCheck()) return false;
    jthrowable thrown = env->ExceptionOccurred();
    env->ExceptionClear();
    jclass object = env->FindClass("java/lang/Object");
    jstring text = static_cast<jstring>(env->CallObjectMethod(
        thrown, env->GetMethodID(object, "toString", "()Ljava/lang/String;")));
    if (env->ExceptionCheck()) env->ExceptionClear();
    const char* chars = text ? env->GetStringUTFChars(text, nullptr) : nullptr;
    *error = chars ? chars : "exception";
    if (chars) env->ReleaseStringUTFChars(text, chars);
    return true;
}

// Redefining a class never reruns its static initializer, so fields such as the
// non-final resource IDs in R classes keep their old values. Initialize the new version
// in a throwaway class loader and copy its static fields onto the live class.
int copy_statics(JNIEnv* env, jobject app_loader, const std::vector<Entry>& entries,
                 std::string* error) {
    std::string path;
    for (const Entry& e : entries) path += (path.empty() ? "" : ":") + e.dex_path;
    jclass path_loader = env->FindClass("dalvik/system/PathClassLoader");
    jobject fresh_loader = env->NewObject(
        path_loader,
        env->GetMethodID(path_loader, "<init>", "(Ljava/lang/String;Ljava/lang/ClassLoader;)V"),
        env->NewStringUTF(path.c_str()), nullptr);
    if (threw(env, error)) return -1;
    jclass klass = env->FindClass("java/lang/Class");
    jmethodID for_name = env->GetStaticMethodID(
        klass, "forName", "(Ljava/lang/String;ZLjava/lang/ClassLoader;)Ljava/lang/Class;");
    jmethodID fields_of = env->GetMethodID(klass, "getDeclaredFields", "()[Ljava/lang/reflect/Field;");
    jmethodID field_of = env->GetMethodID(
        klass, "getDeclaredField", "(Ljava/lang/String;)Ljava/lang/reflect/Field;");
    jclass field = env->FindClass("java/lang/reflect/Field");
    jmethodID name_of = env->GetMethodID(field, "getName", "()Ljava/lang/String;");
    jmethodID modifiers = env->GetMethodID(field, "getModifiers", "()I");
    jmethodID accessible = env->GetMethodID(field, "setAccessible", "(Z)V");
    jmethodID get = env->GetMethodID(field, "get", "(Ljava/lang/Object;)Ljava/lang/Object;");
    jmethodID set = env->GetMethodID(field, "set", "(Ljava/lang/Object;Ljava/lang/Object;)V");
    int copied = 0;
    for (const Entry& e : entries) {
        jstring name = env->NewStringUTF(e.name.c_str());
        jobject fresh = env->CallStaticObjectMethod(klass, for_name, name, JNI_TRUE, fresh_loader);
        if (threw(env, error)) return -1;
        jobject live = env->CallStaticObjectMethod(klass, for_name, name, JNI_TRUE, app_loader);
        if (threw(env, error)) return -1;
        jobjectArray fields = static_cast<jobjectArray>(env->CallObjectMethod(fresh, fields_of));
        if (threw(env, error)) return -1;
        jsize total = env->GetArrayLength(fields);
        for (jsize i = 0; i < total; i++) {
            jobject source = env->GetObjectArrayElement(fields, i);
            jint flags = env->CallIntMethod(source, modifiers);
            // Only static, non-final fields: final constants are compiled into callers.
            if (!(flags & 0x0008) || (flags & 0x0010)) continue;
            jobject target = env->CallObjectMethod(live, field_of, env->CallObjectMethod(source, name_of));
            std::string ignored;
            if (threw(env, &ignored)) continue;
            env->CallVoidMethod(source, accessible, JNI_TRUE);
            env->CallVoidMethod(target, accessible, JNI_TRUE);
            jobject value = env->CallObjectMethod(source, get, nullptr);
            if (threw(env, &ignored)) continue;
            env->CallVoidMethod(target, set, nullptr, value);
            if (!threw(env, &ignored)) copied++;
            env->DeleteLocalRef(source);
            env->DeleteLocalRef(target);
            env->DeleteLocalRef(value);
        }
    }
    return copied;
}

// Ask the Compose runtime to rerun the composable functions with these group keys. The
// compositions stay alive, so remembered state (text fields, scroll positions) survives.
// Uses the runtime's own hot reload entry point; returns -1 when the app has no Compose.
int invalidate_compose(JNIEnv* env, jobject app_loader, const std::vector<jint>& keys,
                       std::string* error) {
    jclass reloader = load_class(env, app_loader, "androidx.compose.runtime.HotReloader");
    if (!reloader) return -1;
    jclass klass = env->FindClass("java/lang/Class");
    jclass field = env->FindClass("java/lang/reflect/Field");
    jclass method = env->FindClass("java/lang/reflect/Method");
    jclass accessible = env->FindClass("java/lang/reflect/AccessibleObject");
    jmethodID set_accessible = env->GetMethodID(accessible, "setAccessible", "(Z)V");
    jobject companion_field = env->CallObjectMethod(
        reloader,
        env->GetMethodID(klass, "getDeclaredField", "(Ljava/lang/String;)Ljava/lang/reflect/Field;"),
        env->NewStringUTF("Companion"));
    if (threw(env, error)) return -2;
    env->CallVoidMethod(companion_field, set_accessible, JNI_TRUE);
    jobject companion = env->CallObjectMethod(
        companion_field,
        env->GetMethodID(field, "get", "(Ljava/lang/Object;)Ljava/lang/Object;"), nullptr);
    if (threw(env, error)) return -2;
    jobjectArray methods = static_cast<jobjectArray>(env->CallObjectMethod(
        env->GetObjectClass(companion),
        env->GetMethodID(klass, "getDeclaredMethods", "()[Ljava/lang/reflect/Method;")));
    if (threw(env, error)) return -2;
    jmethodID name_of = env->GetMethodID(method, "getName", "()Ljava/lang/String;");
    jobject invalidate = nullptr;
    for (jsize i = 0; i < env->GetArrayLength(methods) && !invalidate; i++) {
        jobject one = env->GetObjectArrayElement(methods, i);
        jstring name = static_cast<jstring>(env->CallObjectMethod(one, name_of));
        const char* chars = env->GetStringUTFChars(name, nullptr);
        // Declared internal in Kotlin, so the JVM name carries a module suffix.
        if (!strncmp(chars, "invalidateGroupsWithKey", 23)) invalidate = one;
        env->ReleaseStringUTFChars(name, chars);
    }
    if (!invalidate) {
        *error = "this Compose runtime has no invalidateGroupsWithKey";
        return -2;
    }
    env->CallVoidMethod(invalidate, set_accessible, JNI_TRUE);
    jmethodID invoke = env->GetMethodID(
        method, "invoke", "(Ljava/lang/Object;[Ljava/lang/Object;)Ljava/lang/Object;");
    jclass integer = env->FindClass("java/lang/Integer");
    jmethodID value_of = env->GetStaticMethodID(integer, "valueOf", "(I)Ljava/lang/Integer;");
    jclass object = env->FindClass("java/lang/Object");
    for (jint key : keys) {
        jobjectArray arguments = env->NewObjectArray(1, object,
                                                     env->CallStaticObjectMethod(integer, value_of, key));
        env->CallObjectMethod(invalidate, invoke, companion, arguments);
        if (threw(env, error)) return -2;
        env->DeleteLocalRef(arguments);
    }
    return static_cast<int>(keys.size());
}

jint JNICALL tag_composition(jlong, jlong, jlong* tag, jint, void* wanted) {
    *tag = *static_cast<jlong*>(wanted);
    return JVMTI_VISIT_OBJECTS;
}

// Make every live composition rerun all of its composable functions. This is an
// ordinary recomposition, so everything remembered is kept. Returns -1 without Compose.
int recompose_everything(JNIEnv* env, jvmtiEnv* jvmti, jobject app_loader) {
    jclass composition = load_class(env, app_loader, "androidx.compose.runtime.CompositionImpl");
    if (!composition) return -1;
    jmethodID invalidate_all = env->GetMethodID(composition, "invalidateAll", "()V");
    if (env->ExceptionCheck()) {
        env->ExceptionClear();
        return -1;
    }
    jlong tag = 0x636f6d706f7365;  // "compose"
    jvmtiHeapCallbacks callbacks;
    memset(&callbacks, 0, sizeof callbacks);
    callbacks.heap_iteration_callback = tag_composition;
    jvmti->IterateThroughHeap(0, composition, &callbacks, &tag);
    jint found = 0;
    jobject* objects = nullptr;
    if (jvmti->GetObjectsWithTags(1, &tag, &found, &objects, nullptr) != JVMTI_ERROR_NONE) return -1;
    for (jint i = 0; i < found; i++) {
        jvmti->SetTag(objects[i], 0);
        env->CallVoidMethod(objects[i], invalidate_all);
        if (env->ExceptionCheck()) env->ExceptionClear();
    }
    return found;
}

// An activity whose whole content is one Compose view (what setContent {} builds).
bool is_pure_compose(JNIEnv* env, jobject app_loader, jobject activity) {
    jclass compose_view = load_class(env, app_loader, "androidx.compose.ui.platform.AbstractComposeView");
    if (!compose_view) return false;
    jclass activity_class = env->FindClass("android/app/Activity");
    jobject content = env->CallObjectMethod(
        activity, env->GetMethodID(activity_class, "findViewById", "(I)Landroid/view/View;"),
        0x01020002 /* android.R.id.content */);
    jclass group = env->FindClass("android/view/ViewGroup");
    if (env->ExceptionCheck() || !content || !env->IsInstanceOf(content, group)) {
        env->ExceptionClear();
        return false;
    }
    if (env->CallIntMethod(content, env->GetMethodID(group, "getChildCount", "()I")) != 1)
        return false;
    jobject child = env->CallObjectMethod(
        content, env->GetMethodID(group, "getChildAt", "(I)Landroid/view/View;"), 0);
    return child && env->IsInstanceOf(child, compose_view);
}

jobject g_resources_loader = nullptr;  // the loader installed by the previous swap

// Put a freshly linked resource table in front of the app's own, through the public
// ResourcesLoader API. Contexts created later inherit the application's loaders, so
// rebuilt activities see the new resources too.
bool apply_resources(JNIEnv* env, const std::string& path,
                     const std::vector<jobject>& activities, std::string* error) {
    if (activities.empty()) {
        *error = "no live activity to reach the application through";
        return false;
    }
    jclass file_class = env->FindClass("java/io/File");
    jobject file = env->NewObject(
        file_class, env->GetMethodID(file_class, "<init>", "(Ljava/lang/String;)V"),
        env->NewStringUTF(path.c_str()));
    jclass pfd_class = env->FindClass("android/os/ParcelFileDescriptor");
    jobject pfd = env->CallStaticObjectMethod(
        pfd_class,
        env->GetStaticMethodID(pfd_class, "open", "(Ljava/io/File;I)Landroid/os/ParcelFileDescriptor;"),
        file, 0x10000000 /* MODE_READ_ONLY */);
    if (threw(env, error)) return false;
    jclass provider_class = env->FindClass("android/content/res/loader/ResourcesProvider");
    if (threw(env, error)) {
        *error = "resource swap needs Android 11+";
        return false;
    }
    jobject provider = env->CallStaticObjectMethod(
        provider_class,
        env->GetStaticMethodID(provider_class, "loadFromApk",
            "(Landroid/os/ParcelFileDescriptor;)Landroid/content/res/loader/ResourcesProvider;"),
        pfd);
    if (threw(env, error)) return false;
    env->CallVoidMethod(pfd, env->GetMethodID(pfd_class, "close", "()V"));
    if (env->ExceptionCheck()) env->ExceptionClear();
    jclass loader_class = env->FindClass("android/content/res/loader/ResourcesLoader");
    jobject loader = env->NewObject(loader_class, env->GetMethodID(loader_class, "<init>", "()V"));
    env->CallVoidMethod(
        loader,
        env->GetMethodID(loader_class, "addProvider",
                         "(Landroid/content/res/loader/ResourcesProvider;)V"),
        provider);
    if (threw(env, error)) return false;

    jclass context = env->FindClass("android/content/Context");
    jmethodID resources_of = env->GetMethodID(context, "getResources", "()Landroid/content/res/Resources;");
    jclass resources = env->FindClass("android/content/res/Resources");
    const char* signature = "([Landroid/content/res/loader/ResourcesLoader;)V";
    jmethodID add = env->GetMethodID(resources, "addLoaders", signature);
    jmethodID remove = env->GetMethodID(resources, "removeLoaders", signature);
    jclass activity = env->FindClass("android/app/Activity");
    jobject application = env->CallObjectMethod(
        activities[0], env->GetMethodID(activity, "getApplication", "()Landroid/app/Application;"));
    std::vector<jobject> contexts = activities;
    contexts.push_back(application);
    jobjectArray fresh = env->NewObjectArray(1, loader_class, loader);
    jobjectArray stale =
        g_resources_loader ? env->NewObjectArray(1, loader_class, g_resources_loader) : nullptr;
    for (jobject one : contexts) {
        jobject target = env->CallObjectMethod(one, resources_of);
        if (stale) env->CallVoidMethod(target, remove, stale);
        env->CallVoidMethod(target, add, fresh);
        if (threw(env, error)) return false;
    }
    if (g_resources_loader) env->DeleteGlobalRef(g_resources_loader);
    g_resources_loader = env->NewGlobalRef(loader);
    return true;
}

}  // namespace

extern "C" JNIEXPORT jint JNICALL Agent_OnAttach(JavaVM* vm, char* options, void*) {
    std::string plan = options ? options : "";
    std::vector<Entry> entries;
    std::string refresh = "none";
    std::vector<jint> compose_keys;
    std::string resources, helper_dex;
    std::vector<Entry> statics;
    {
        FILE* f = fopen(plan.c_str(), "r");
        if (!f) return report(plan, false, "cannot read plan " + plan);
        char a[32], b[1024], c[1024];
        char line[2200];
        while (fgets(line, sizeof line, f)) {
            int n = sscanf(line, "%31s %1023s %1023s", a, b, c);
            if (n == 2 && !strcmp(a, "refresh")) refresh = b;
            else if (n == 2 && !strcmp(a, "compose")) compose_keys.push_back(atoi(b));
            else if (n == 2 && !strcmp(a, "resources")) resources = b;
            else if (n == 2 && !strcmp(a, "helper")) helper_dex = b;
            else if (n == 3 && !strcmp(a, "class")) entries.push_back({b, c});
            else if (n == 3 && !strcmp(a, "statics")) statics.push_back({b, c});
        }
        fclose(f);
    }
    if (entries.empty() && resources.empty()) return report(plan, false, "plan is empty");

    jvmtiEnv* jvmti = nullptr;
    JNIEnv* env = nullptr;
    if (vm->GetEnv(reinterpret_cast<void**>(&jvmti), JVMTI_VERSION_1_2) != JNI_OK ||
        vm->GetEnv(reinterpret_cast<void**>(&env), JNI_VERSION_1_6) != JNI_OK)
        return report(plan, false, "no JVMTI/JNI environment");
    jvmtiCapabilities caps;
    memset(&caps, 0, sizeof caps);
    caps.can_redefine_classes = 1;
    caps.can_tag_objects = 1;
    jvmtiError err = jvmti->AddCapabilities(&caps);
    Extensions ext = find_extensions(jvmti);
    if (err != JVMTI_ERROR_NONE)
        return report(plan, false, "AddCapabilities " + error_text(jvmti, ext, err));

    jint count = 0;
    jclass* classes = nullptr;
    jvmti->GetLoadedClasses(&count, &classes);
    jobject loader = app_class_loader(env, jvmti, count, classes);
    if (!loader) return report(plan, false, "app class loader not found");

    for (Entry& e : entries) {
        if (!read_file(e.dex_path, &e.dex)) return report(plan, false, "cannot read " + e.dex_path);
        std::string signature = "L" + e.name + ";";
        for (char& ch : signature) if (ch == '.') ch = '/';
        for (jint i = 0; i < count && !e.klass; i++) {
            char* sig = nullptr;
            if (jvmti->GetClassSignature(classes[i], &sig, nullptr) != JVMTI_ERROR_NONE || !sig)
                continue;
            if (signature == sig) e.klass = classes[i];
            jvmti->Deallocate(reinterpret_cast<unsigned char*>(sig));
        }
        // Not loaded yet: the app would otherwise load the stale copy from its APK later.
        if (!e.klass) e.klass = load_class(env, loader, e.name);
    }

    // New classes first, so redefined code can resolve them.
    int added = 0;
    for (Entry& e : entries) {
        if (e.klass) continue;
        if (!ext.add_dex_file)
            return report(plan, false, "new class " + e.name + " needs a newer Android (no add-dex extension)");
        // By file, not from memory: Android 14+ refuses dex code from a writable source,
        // so the host leaves these files read-only.
        err = ext.add_dex_file(jvmti, loader, e.dex_path.c_str());
        if (err != JVMTI_ERROR_NONE)
            return report(plan, false, "add " + e.name + " " + error_text(jvmti, ext, err));
        added++;
    }

    std::vector<jvmtiClassDefinition> defs;
    for (Entry& e : entries) {
        if (!e.klass) continue;
        jvmtiClassDefinition def;
        def.klass = e.klass;
        def.class_byte_count = static_cast<jint>(e.dex.size());
        def.class_bytes = e.dex.data();
        defs.push_back(def);
    }
    bool structural = false;
    if (!defs.empty()) {
        // All classes go in one call: either every one is replaced or none is.
        err = jvmti->RedefineClasses(static_cast<jint>(defs.size()), defs.data());
        if (err != JVMTI_ERROR_NONE && ext.structural_redefine) {
            std::string first = error_text(jvmti, ext, err);
            err = ext.structural_redefine(jvmti, static_cast<jint>(defs.size()), defs.data());
            structural = err == JVMTI_ERROR_NONE;
            if (!structural)
                return report(plan, false, "redefine " + first + " | structural " +
                                               error_text(jvmti, ext, err));
        } else if (err != JVMTI_ERROR_NONE) {
            return report(plan, false, "redefine " + error_text(jvmti, ext, err));
        }
    }

    int copied = 0;
    if (!statics.empty()) {
        std::string error;
        copied = copy_statics(env, loader, statics, &error);
        if (copied < 0) return report(plan, false, "statics " + error);
    }

    // Code is already swapped at this point; the UI steps need the main thread.
    int recreated = 0, recomposed = 0;
    bool helper_loaded = false;
    if (refresh != "none" || !resources.empty()) {
        if (!on_main_thread(env)) return report(plan, false, "agent was not attached on the main thread");
        std::vector<jobject> activities = live_activities(env, jvmti);
        std::string error;
        if (!resources.empty() && !apply_resources(env, resources, activities, &error))
            return report(plan, false, "resources " + error);
        jclass helper = load_class(env, loader, "hotswap.agent.HotswapState");
        if (!helper && !helper_dex.empty() && ext.add_dex_file &&
            ext.add_dex_file(jvmti, loader, helper_dex.c_str()) == JVMTI_ERROR_NONE)
            helper = load_class(env, loader, "hotswap.agent.HotswapState");
        helper_loaded = helper != nullptr;
        if (refresh == "auto") {
            // Compose screens rerun their functions in place and keep all state. Other
            // activities are rebuilt, with their view state saved and put back.
            if (!compose_keys.empty() && invalidate_compose(env, loader, compose_keys, &error) == -2)
                LOG("could not reset changed composables: %s", error.c_str());
            recomposed = recompose_everything(env, jvmti, loader);
            std::vector<jobject> rebuild;
            for (jobject one : activities)
                if (recomposed < 0 || !is_pure_compose(env, loader, one)) rebuild.push_back(one);
            if (recomposed < 0) recomposed = 0;
            recreated = recreate_activities(env, rebuild, helper);
        } else if (refresh == "recreate") {
            recreated = recreate_activities(env, activities, helper);
        }
    }
    return report(plan, true,
                  "redefined=" + std::to_string(defs.size()) + " added=" + std::to_string(added) +
                      " structural=" + (structural ? "1" : "0") +
                      " statics=" + std::to_string(copied) +
                      " resources=" + (resources.empty() ? "0" : "1") +
                      " recomposed=" + std::to_string(recomposed) +
                      " helper=" + (helper_loaded ? "1" : "0") +
                      " recreated=" + std::to_string(recreated));
}
