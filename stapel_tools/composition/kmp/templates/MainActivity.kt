package {{PKG}}.android

import android.app.Application
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import {{PKG}}.App
import {{PKG}}.createAndroidApp
{{ACTIVITY_IMPORTS}}import stapel.app.di.StapelApp

/** Holds the one app instance. */
class MainApplication : Application() {
    lateinit var app: StapelApp
        private set

    override fun onCreate() {
        super.onCreate()
        app = createAndroidApp(this)
    }
}

/** The single activity: the Compose app, and deep links forwarded to its navigator. */
class MainActivity : ComponentActivity() {
    private val app get() = (application as MainApplication).app

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        intent?.data?.let { app.openUrl(it.toString()) }
        setContent { App(app) }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
{{PUSH_INTENT}}        intent.data?.let { app.openUrl(it.toString()) }
    }
}
