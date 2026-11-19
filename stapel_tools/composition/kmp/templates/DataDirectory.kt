package {{PKG}}

/**
 * The directory the durable cache keeps its databases in: null on Android and iOS (the platform
 * default: `noBackupFilesDir`, Application Support), the per-user application data directory on
 * desktop, which has no platform default.
 */
internal expect fun platformDataDirectory(): String?
