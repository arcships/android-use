package hotswap.agent;

import android.app.Activity;
import android.app.Application;
import android.content.Context;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.view.ViewGroup;
import android.view.ViewTreeObserver;
import android.view.WindowInsets;
import android.view.inputmethod.InputMethodManager;
import android.widget.EditText;
import java.util.ArrayList;
import java.util.List;

/**
 * Loaded into the app by the hot swap agent. Rebuilds an activity and puts back the view
 * state the platform does not restore by itself: scroll offsets (which restoring focus
 * otherwise overrides), the text of input fields that have no ID, and an open keyboard.
 */
public final class HotswapState implements Application.ActivityLifecycleCallbacks {
    private static final class Entry {
        String path;
        String type;
        int scrollX, scrollY, selection;
        CharSequence text;
    }

    private final Application application;
    private final String activityClass;
    private final List<Entry> entries = new ArrayList<>();
    private boolean keyboardShown;
    private final Handler handler = new Handler(Looper.getMainLooper());

    private HotswapState(Activity activity) {
        application = activity.getApplication();
        activityClass = activity.getClass().getName();
        View root = activity.getWindow().getDecorView();
        capture(root, "");
        if (Build.VERSION.SDK_INT >= 30) {
            WindowInsets insets = root.getRootWindowInsets();
            keyboardShown = insets != null && insets.isVisible(WindowInsets.Type.ime());
        }
    }

    /** Called by the agent on the main thread in place of Activity.recreate(). */
    public static void recreate(Activity activity) {
        final HotswapState state = new HotswapState(activity);
        state.application.registerActivityLifecycleCallbacks(state);
        // Never linger if the rebuilt activity does not come back.
        state.handler.postDelayed(new Runnable() {
            @Override
            public void run() {
                state.application.unregisterActivityLifecycleCallbacks(state);
            }
        }, 10000);
        activity.recreate();
    }

    private void capture(View view, String path) {
        boolean unsavedText = view instanceof EditText && view.getId() == View.NO_ID;
        if (view.getScrollX() != 0 || view.getScrollY() != 0 || unsavedText) {
            Entry entry = new Entry();
            entry.path = path;
            entry.type = view.getClass().getName();
            entry.scrollX = view.getScrollX();
            entry.scrollY = view.getScrollY();
            if (unsavedText) {
                entry.text = ((EditText) view).getText().toString();
                entry.selection = ((EditText) view).getSelectionEnd();
            }
            entries.add(entry);
        }
        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) view;
            for (int i = 0; i < group.getChildCount(); i++) capture(group.getChildAt(i), path + "/" + i);
        }
    }

    private void restore(View root, boolean withText) {
        for (Entry entry : entries) {
            View view = root;
            for (String step : entry.path.split("/")) {
                if (step.isEmpty()) continue;
                int index = Integer.parseInt(step);
                if (!(view instanceof ViewGroup) || index >= ((ViewGroup) view).getChildCount()) {
                    view = null;
                    break;
                }
                view = ((ViewGroup) view).getChildAt(index);
            }
            // Only when the same kind of view is still in the same place.
            if (view == null || !view.getClass().getName().equals(entry.type)) continue;
            if (withText && entry.text != null) {
                EditText input = (EditText) view;
                input.setText(entry.text);
                input.setSelection(Math.min(Math.max(entry.selection, 0), input.length()));
            }
            view.scrollTo(entry.scrollX, entry.scrollY);
        }
    }

    @Override
    public void onActivityResumed(final Activity activity) {
        if (!activity.getClass().getName().equals(activityClass)) return;
        application.unregisterActivityLifecycleCallbacks(this);
        final View root = activity.getWindow().getDecorView();
        // After the first layout pass, when scroll ranges are known and focus has settled.
        root.getViewTreeObserver().addOnPreDrawListener(new ViewTreeObserver.OnPreDrawListener() {
            @Override
            public boolean onPreDraw() {
                root.getViewTreeObserver().removeOnPreDrawListener(this);
                restore(root, true);
                View focused = activity.getCurrentFocus();
                if (keyboardShown && focused != null) {
                    ((InputMethodManager) activity.getSystemService(Context.INPUT_METHOD_SERVICE))
                            .showSoftInput(focused, 0);
                }
                return true;
            }
        });
        // While the window settles the platform keeps moving scroll offsets: focus is
        // scrolled into view, and a focused input field pulls its cursor back on screen
        // just before drawing. Hold the saved offsets for every frame of that window.
        final ViewTreeObserver.OnPreDrawListener hold = new ViewTreeObserver.OnPreDrawListener() {
            @Override
            public boolean onPreDraw() {
                restore(root, false);
                return true;
            }
        };
        root.getViewTreeObserver().addOnPreDrawListener(hold);
        handler.postDelayed(new Runnable() {
            @Override
            public void run() {
                if (root.getViewTreeObserver().isAlive()) {
                    root.getViewTreeObserver().removeOnPreDrawListener(hold);
                }
                restore(root, false);
            }
        }, 1500);
    }

    @Override public void onActivityCreated(Activity activity, Bundle state) {}
    @Override public void onActivityStarted(Activity activity) {}
    @Override public void onActivityPaused(Activity activity) {}
    @Override public void onActivityStopped(Activity activity) {}
    @Override public void onActivitySaveInstanceState(Activity activity, Bundle state) {}
    @Override public void onActivityDestroyed(Activity activity) {}
}
