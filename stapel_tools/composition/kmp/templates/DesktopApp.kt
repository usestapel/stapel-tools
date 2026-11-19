package {{PKG}}

import stapel.app.di.StapelApp
import stapel.runtime.PlatformContext

/** The desktop app. */
fun createDesktopApp(baseUrl: String? = null): StapelApp = createApp(PlatformContext(), "desktop", baseUrl)
