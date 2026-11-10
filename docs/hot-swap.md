# Hot swap: apply source edits to the running app

`aua_hot_swap` is for developing an Android app whose source is on the same computer. After an edit, one call compiles what changed and redefines the classes inside the running process. The app is not reinstalled or restarted, so the screen the agent was looking at stays where it was. With the live screen open, the change shows up there about a second later.

It uses the JDK and the Android SDK directly (javac, the Kotlin compiler, D8, aapt2, a small JVMTI agent). There is no Gradle plugin and nothing is added to the app's build.

## Use

```bash
# What would happen, and is the host ready? Changes nothing.
uv run android-use hot_swap '{"project":"/path/to/MyApp","action":"status"}'

# After each edit. The first call does the full build, install and launch.
uv run android-use hot_swap '{"project":"/path/to/MyApp","observe":"screenshot"}'
```

| Argument | Meaning |
| --- | --- |
| `project` | Absolute path of a Gradle root, of a module directory below it, or of a directory with a `hotswap.json`. |
| `action` | `swap` (default), `build` (force the full build, install and launch) or `status`. |
| `refresh` | `auto` (default), `recreate` (rebuild every activity) or `none` (leave the UI alone). |
| `module`, `variant` | Gradle only; default `:app` and `Debug`. Remembered in `build/hotswap-config.json`; changing one forces a full build. |
| `observe` | `none` (default), `tree`, `screenshot` or `both`: return the screen after the swap in the same call. |

The result is one of:

- `swapped`: classes and/or resources were replaced in place. `applied` says how many classes were redefined or added, whether Compose recomposed and whether an activity was rebuilt.
- `installed`: a full build ran instead; `reason` says why (first call, the app was not running, a manifest or build file changed, a source file was deleted, the runtime refused the swap).
- `unchanged`: nothing differs from what the process runs.

A compile error returns `compile_failed` with the compiler output and leaves the app as it was. Fix the source and call again. `verified` is always `false`: the tool reports what was loaded, the agent checks the UI.

## What is kept

- Compose: the functions run again over their existing state. `remember` values and scroll positions stay. A structural change to a composable (calls added or removed) resets that function's state only.
- Views: the activity is rebuilt with its saved state, plus scroll offsets, the text of input fields without an ID, and the keyboard. Instance fields of the activity are not kept.
- The process: statics, singletons, the back stack, the login session.

## Requirements and limits

- The app must be debuggable (`android:debuggable="true"`, the default for debug builds) and the device must run Android 8 or later. Resource edits need Android 11 or later.
- Host: a JDK on `PATH`, the Android SDK (`ANDROID_HOME`, or the default location) with build-tools and a platform. The NDK is needed once per device ABI to compile the agent; the result is cached under the Android Use state directory. macOS and Linux.
- Gradle projects are detected from `settings.gradle(.kts)`. Gradle does the full builds; the swaps reuse the classpath and compiler flags it exports. Settings and caches go to the project's `build/` directory.
- A plain project describes itself in `hotswap.json`; see `script/hotswap/app/hotswap.json`.
- Not yet covered: annotation processors (kapt, KSP, Hilt, Room) that must rerun for an edit, edits spanning several Gradle modules, Kotlin 2.x. Those edits still work through the full-build fallback when the swap is refused, but an edit that needs regenerated code should use `action=build`.
- Paused devices (authentication handoff) refuse `swap` and `build`.

## Check it on a device

`script/hotswap/verify.py [-s SERIAL] [--gradle]` builds the lab apps in `script/hotswap/`, edits their sources and confirms from the app's own log what the running process executes: method bodies, new methods, fields and classes, lambdas, Kotlin inline functions, resource values, colours and new resources, and that typed text, scroll offsets and Compose state survive.
