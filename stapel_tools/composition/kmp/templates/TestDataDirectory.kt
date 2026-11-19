package {{PKG}}

/** Where tests keep the durable cache: a fresh temporary directory on the JVM, the platform default on iOS. */
expect fun testDataDirectory(): String?
