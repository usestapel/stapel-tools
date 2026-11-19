package {{PKG}}

import androidx.compose.ui.window.ComposeUIViewController
import platform.UIKit.UIViewController
import stapel.app.di.StapelApp
import stapel.runtime.PlatformContext

private val app: StapelApp by lazy { createApp(PlatformContext(), "ios") }

/** The Compose root the Swift host embeds. */
fun MainViewController(): UIViewController = ComposeUIViewController { App(app) }

/** Forwards a URL the system opened the app with (`{{SCHEME}}://…`, universal links). */
fun openUrl(url: String): Boolean = app.openUrl(url)
