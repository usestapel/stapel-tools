package {{PKG}}

import android.app.Application
{{ANDROID_IMPORTS}}import stapel.app.di.StapelApp
import stapel.runtime.PlatformContext

/** The Android app, created once in `Application.onCreate`. */
fun createAndroidApp(application: Application): StapelApp = createApp(PlatformContext(application), "android")
{{ANDROID_EXTRA}}