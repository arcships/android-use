package io.github.androiduse.acceptance;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.view.GestureDetector;
import android.view.MotionEvent;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.EditText;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;

/** Isolated, permission-free fixture. Counters are produced by Android UI events. */
public class MainActivity extends Activity {
    private TextView longState, doubleState, dragState, target;
    private int longs, doubles, drags;
    private final Handler handler = new Handler();

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }

    private TextView label(String text) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextSize(16);
        view.setTextColor(Color.BLACK);
        return view;
    }

    private Button button(String text, String description) {
        Button view = new Button(this);
        view.setText(text);
        view.setContentDescription(description);
        return view;
    }

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        if (getIntent().getBooleanExtra("benchmark", false)) {
            benchmarkMenu();
            return;
        }
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(12), dp(12), dp(12), dp(12));
        root.setBackgroundColor(Color.WHITE);
        root.addView(label("Android Use / interaction acceptance"));
        longState = label("LONG_PRESSES=0"); root.addView(longState);
        doubleState = label("DOUBLE_TAPS=0"); root.addView(doubleState);
        dragState = label("DRAGS=0"); root.addView(dragState);
        LinearLayout buttons = new LinearLayout(this);
        Button show = button("Show", "show-target");
        Button hide = button("Hide", "hide-target");
        Button longButton = button("Long press", "long-target");
        buttons.addView(show); buttons.addView(hide); buttons.addView(longButton);
        root.addView(buttons);
        target = label("TARGET_VISIBLE");
        target.setVisibility(View.GONE);
        root.addView(target);
        show.setOnClickListener(v -> handler.postDelayed(() -> target.setVisibility(View.VISIBLE), 450));
        hide.setOnClickListener(v -> handler.postDelayed(() -> target.setVisibility(View.GONE), 450));
        longButton.setOnLongClickListener(v -> {
            longState.setText("LONG_PRESSES=" + (++longs));
            return true;
        });
        root.addView(new GesturePad(), new LinearLayout.LayoutParams(-1, dp(140)));
        ScrollView scroll = new ScrollView(this);
        scroll.setContentDescription("scroll-list");
        LinearLayout rows = new LinearLayout(this);
        rows.setOrientation(LinearLayout.VERTICAL);
        for (int i = 0; i < 20; i++) {
            TextView row = label("Row " + i);
            row.setPadding(dp(8), dp(8), 0, 0);
            rows.addView(row, new LinearLayout.LayoutParams(-1, dp(60)));
        }
        scroll.addView(rows);
        root.addView(scroll, new LinearLayout.LayoutParams(-1, dp(220)));
        setContentView(root);
    }

    private LinearLayout benchmarkRoot(String title) {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(16), dp(16), dp(16), dp(16));
        root.setBackgroundColor(Color.WHITE);
        root.addView(label(title));
        setContentView(root);
        return root;
    }

    private void benchmarkMenu() {
        LinearLayout root = benchmarkRoot("Android Use / decision benchmark");
        Button profile = button("Profile", "open-profile");
        Button catalog = button("Catalog", "open-catalog");
        Button status = button("Status", "open-status");
        Button workflow = button("Workflow", "open-workflow");
        root.addView(profile); root.addView(catalog); root.addView(status); root.addView(workflow);
        workflow.setOnClickListener(v -> {
            LinearLayout page = benchmarkRoot("Workflow: follow the current instruction");
            TextView instruction = label("");
            TextView result = label("WORKFLOW_PENDING");
            page.addView(instruction); page.addView(result);
            final String[] colors = {"Blue", "Green", "Red"};
            final int[] next = new int[4];
            java.util.Random random = new java.util.Random(getIntent().getIntExtra("seed", 41));
            for (int i = 0; i < next.length; i++) next[i] = random.nextInt(colors.length);
            final int[] stage = {0};
            instruction.setText("Next color: " + colors[next[0]] + " (step 1/4)");
            for (int i = 0; i < colors.length; i++) {
                final int selected = i;
                Button color = button(colors[i], "choose-" + colors[i].toLowerCase(java.util.Locale.ROOT));
                page.addView(color);
                color.setOnClickListener(w -> {
                    if (stage[0] >= next.length || stage[0] < 0) return;
                    if (selected != next[stage[0]]) {
                        stage[0] = -1;
                        instruction.setText("Wrong selection; restart required");
                        result.setText("WORKFLOW_FAILED");
                    } else if (++stage[0] == next.length) {
                        instruction.setText("All instructions completed");
                        result.setText("WORKFLOW_DONE");
                    } else {
                        instruction.setText("Next color: " + colors[next[stage[0]]]
                            + " (step " + (stage[0] + 1) + "/4)");
                    }
                });
            }
        });
        profile.setOnClickListener(v -> {
            LinearLayout page = benchmarkRoot("Profile draft (local only)");
            EditText name = new EditText(this);
            name.setContentDescription("draft-name");
            name.setHint("Name");
            // Native setText is the tested input path; IME is not needed in this fixture.
            name.setShowSoftInputOnFocus(false);
            page.addView(name);
            TextView result = label("DRAFT_EMPTY");
            Button apply = button("Apply draft", "apply-draft");
            page.addView(apply); page.addView(result);
            apply.setOnClickListener(w -> result.setText("DRAFT=" + name.getText()));
        });
        catalog.setOnClickListener(v -> {
            LinearLayout page = benchmarkRoot("Catalog");
            TextView selected = label("NOT_SELECTED");
            page.addView(selected);
            ScrollView scroll = new ScrollView(this);
            scroll.setContentDescription("catalog-list");
            LinearLayout rows = new LinearLayout(this);
            rows.setOrientation(LinearLayout.VERTICAL);
            for (int i = 0; i < 24; i++) {
                final String value = "Item " + i;
                Button item = button(value, "select-" + i);
                rows.addView(item, new LinearLayout.LayoutParams(-1, dp(64)));
                item.setOnClickListener(w -> selected.setText("SELECTED=" + value));
            }
            scroll.addView(rows);
            page.addView(scroll, new LinearLayout.LayoutParams(-1, dp(360)));
        });
        status.setOnClickListener(v -> {
            LinearLayout page = benchmarkRoot("Status");
            TextView result = label("PENDING");
            Button show = button("Show result", "show-result");
            Button hold = button("Hold to confirm", "hold-confirm");
            page.addView(show); page.addView(hold); page.addView(result);
            show.setOnClickListener(w -> handler.postDelayed(() -> result.setText("RESULT_READY"), 450));
            hold.setOnLongClickListener(w -> { result.setText("HOLD_CONFIRMED"); return true; });
        });
    }

    private class GesturePad extends View {
        private final Paint paint = new Paint(3);
        private float startX, startY, dotX = dp(40), dotY = dp(70);
        private final GestureDetector detector;

        GesturePad() {
            super(MainActivity.this);
            setContentDescription("gesture-pad");
            setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_YES);
            detector = new GestureDetector(MainActivity.this, new GestureDetector.SimpleOnGestureListener() {
                @Override public boolean onDown(MotionEvent event) { return true; }
                @Override public boolean onDoubleTap(MotionEvent event) {
                    doubleState.setText("DOUBLE_TAPS=" + (++doubles));
                    return true;
                }
            });
        }

        @Override protected void onDraw(Canvas canvas) {
            canvas.drawColor(0xffe5efff);
            paint.setColor(0xff244ea0);
            canvas.drawCircle(dotX, dotY, dp(14), paint);
            paint.setTextSize(dp(15));
            canvas.drawText("Double tap / drag inside this pad", dp(8), dp(25), paint);
        }

        @Override public boolean onTouchEvent(MotionEvent event) {
            detector.onTouchEvent(event);
            if (event.getActionMasked() == MotionEvent.ACTION_DOWN) {
                startX = event.getX(); startY = event.getY();
            } else if (event.getActionMasked() == MotionEvent.ACTION_MOVE) {
                dotX = event.getX(); dotY = event.getY(); invalidate();
            } else if (event.getActionMasked() == MotionEvent.ACTION_UP
                    && Math.hypot(event.getX() - startX, event.getY() - startY) > dp(30)) {
                dotX = event.getX(); dotY = event.getY(); invalidate();
                dragState.setText("DRAGS=" + (++drags));
            }
            return true;
        }
    }
}
