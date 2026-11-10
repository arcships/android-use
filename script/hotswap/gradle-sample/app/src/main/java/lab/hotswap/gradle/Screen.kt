package lab.hotswap.gradle

import android.os.Process
import android.util.Log
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay

@Composable
fun Screen() {
    var ticks by remember { mutableIntStateOf(0) }
    // Plain remember, not saveable: lost if the composition is thrown away.
    var local by remember { mutableIntStateOf(0) }
    val scroll = rememberScrollState()
    val context = LocalContext.current
    // Recorded during composition, so the log proves which composable body ran.
    Counter.label = "compose A"
    LaunchedEffect(Unit) {
        while (true) {
            delay(500)
            ticks = ++Counter.ticks
            local++
            Log.i(
                "hotswap-app",
                "${Counter.label} | ${Labels.title()} | ${JavaMessage.text()} | " +
                    "${context.getString(R.string.title)} | " +
                    "ticks=${Counter.ticks} creations=${Counter.creations} pid=${Process.myPid()} " +
                    "local=$local scroll=${scroll.value}",
            )
        }
    }
    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(scroll),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(Counter.label)
        Text(Labels.title())
        Text(JavaMessage.text())
        Text(stringResource(R.string.title))
        Text("ticks=$ticks local=$local")
        Spacer(Modifier.height(2000.dp))
    }
}
