package lab.hotswap.gradle

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent

/** Process state: survives a hot swap, not a process restart. */
object Counter {
    var ticks = 0
    var creations = 0
    var label = ""
}

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Counter.creations++
        setContent { Screen() }
    }
}
