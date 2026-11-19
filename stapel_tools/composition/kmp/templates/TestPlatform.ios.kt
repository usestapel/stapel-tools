package {{PKG}}

import stapel.runtime.PlatformContext

actual fun testPlatformContext(): PlatformContext = PlatformContext()

actual val testTarget: String = "ios"
