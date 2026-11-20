package {{PKG}}

{{BRIDGE_IMPORTS}}
{{BRIDGE_VARS}}
/**
 * Hands the app shell's Swift bridges (`iosApp/iosApp/Stapel/`) to Kotlin; `iOSApp.swift` calls it
 * before the first screen creates the app. A null bridge leaves its provider reporting unconfigured.
 */
fun installBridges({{BRIDGE_PARAMS}}) {
{{BRIDGE_ASSIGN}}}
{{BRIDGE_EXTRA}}
