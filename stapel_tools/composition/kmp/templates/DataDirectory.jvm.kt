package {{PKG}}

import java.io.File

/** macOS `~/Library/Application Support/<app id>`, Windows `%APPDATA%\\<app id>`, else `$XDG_DATA_HOME|~/.local/share/<app id>`. */
internal actual fun platformDataDirectory(): String? {
    val home = System.getProperty("user.home")
    val os = System.getProperty("os.name").lowercase()
    val base = when {
        os.contains("mac") -> File(home, "Library/Application Support")
        os.contains("win") -> File(System.getenv("APPDATA") ?: home)
        else -> File(System.getenv("XDG_DATA_HOME") ?: "$home/.local/share")
    }
    return File(base, "{{APP_ID}}").absolutePath
}
