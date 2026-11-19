package {{PKG}}.desktop

import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.window.Window
import androidx.compose.ui.window.application
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withTimeoutOrNull
import {{PKG}}.App
import {{PKG}}.createDesktopApp
import kotlin.system.exitProcess

/** The desktop app. `--smoke`: exit 0 once the navigator exists and the first destination is shown. */
fun main(args: Array<String>) {
    val app = createDesktopApp(System.getProperty("app.baseUrl"))
    val smoke = "--smoke" in args
    application {
        Window(onCloseRequest = { app.close(); exitApplication() }, title = {{TITLE_KT_STRING}}) {
            App(app)
        }
        if (smoke) {
            LaunchedEffect(Unit) {
                val ready = withTimeoutOrNull(30_000) {
                    val navigator = app.navigator.first { it != null }!!
                    navigator.state.first { it.top != null }
                    delay(500)
                    true
                } ?: false
                println(if (ready) "app.smoke ok: ${app.navigator.value?.state?.value?.top?.route}" else "app.smoke FAILED: no destination within 30 s")
                app.close()
                exitProcess(if (ready) 0 else 1)
            }
        }
    }
}
