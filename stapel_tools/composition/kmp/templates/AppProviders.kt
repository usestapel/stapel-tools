package {{PKG}}

import org.koin.core.module.Module

/**
 * The vendor providers of this platform (stapel.composition.json "analytics" / "push"): each
 * provider artifact's Koin module, over the Swift bridges on iOS. Without its config file a
 * provider is a no-op that says so (`stapel.analytics unavailable <sink> unconfigured <file>`,
 * `stapel.push unavailable …`); the app starts either way.
 */
internal expect fun platformProviderModules(): List<Module>
