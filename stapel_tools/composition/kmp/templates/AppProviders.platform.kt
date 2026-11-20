package {{PKG}}

import org.koin.core.module.Module
{{PROVIDER_IMPORTS}}
internal actual fun platformProviderModules(): List<Module> = listOf(
{{PROVIDER_CALLS}})
