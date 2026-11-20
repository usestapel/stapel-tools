package {{PKG}}

import org.koin.dsl.module
{{GATE_IMPORTS}}import stapel.session.vault.InMemoryTokenVault
import stapel.session.vault.TokenVault
import kotlin.test.Test
import kotlin.test.assertEquals

/**
 * The providers the running app resolves equal what the scaffold planned into
 * stapel.composition.json ("analytics" sinks, "push" providers). No backend, no vendor config.
 */
class ProvidersGateTest {
    @Test
    fun runtimeProvidersEqualThePlannedComposition() {
        val app = createApp(testPlatformContext(), testTarget, "http://127.0.0.1:9/"{{TEST_DATA_ARG}}) {
            overrides(module { single<TokenVault> { InMemoryTokenVault() } })
            global(false)
        }
        try {
{{GATE_ASSERTS}}        } finally {
            app.close()
        }
    }
}
