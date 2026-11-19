package {{PKG}}

import stapel.runtime.PlatformContext

/** The platform handle in tests (jvm and the iOS simulator). */
expect fun testPlatformContext(): PlatformContext

/** `desktop` on the JVM, `ios` on the simulator. */
expect val testTarget: String
