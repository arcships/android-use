package lab.hotswap;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Process;
import android.util.Log;
import android.widget.EditText;
import android.widget.ScrollView;
import android.widget.TextView;

public class MainActivity extends Activity {
    // Process state: survives a hot swap (even with the activity rebuilt), not a restart.
    static int ticks;
    static int creations;
    private TextView status;
    private EditText input;
    private ScrollView scroll;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable tick = new Runnable() {
        @Override
        public void run() {
            ticks++;
            String line = Message.text() + " | " + Greeter.INSTANCE.greet() + " | "
                    + getString(R.string.title);
            status.setText(line.replace(" | ", "\n") + "\nticks=" + ticks + " creations=" + creations
                    + "\npid=" + Process.myPid());
            // Machine-readable evidence for the experiment, including the view state a
            // rebuilt activity has to bring back: typed text and scroll position.
            Log.i("hotswap-app", line + " | ticks=" + ticks + " creations=" + creations
                    + " pid=" + Process.myPid() + " input=" + input.getText() + " scroll="
                    + scroll.getScrollY() + " color="
                    + Integer.toHexString(status.getCurrentTextColor()));
            handler.postDelayed(this, 500);
        }
    };

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        creations++;
        setContentView(R.layout.main);
        status = findViewById(R.id.status);
        input = findViewById(R.id.input);
        scroll = findViewById(R.id.scroll);
        if (state == null) {
            input.requestFocus();
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        handler.post(tick);
    }

    @Override
    protected void onPause() {
        handler.removeCallbacks(tick);
        super.onPause();
    }
}
