package {{PKG}}

actual fun testDataDirectory(): String? = kotlin.io.path.createTempDirectory("{{SLUG}}-test").toString()
