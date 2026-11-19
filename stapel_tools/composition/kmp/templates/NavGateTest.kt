package {{PKG}}

import org.koin.dsl.module
import stapel.navigation.resolveNav
import stapel.session.vault.InMemoryTokenVault
import stapel.session.vault.TokenVault
import kotlin.test.Test
import kotlin.test.assertEquals

/**
 * The runtime navigation equals what the scaffold planned into stapel.composition.json (`routes`,
 * `signIn`, the shell slots). No backend needed.
 */
class NavGateTest {
    private val planned: Map<String, String> = mapOf({{PLANNED_ROUTES}})
    private val plannedSlots: Map<String, List<String>> = mapOf({{PLANNED_SLOTS}})

    @Test
    fun runtimeNavigationEqualsThePlannedComposition() {
        val app = createApp(testPlatformContext(), testTarget, "http://127.0.0.1:9/"{{TEST_DATA_ARG}}) {
            overrides(module { single<TokenVault> { InMemoryTokenVault() } })
            global(false)
        }
        try {
            val nav = resolveNav(app.contributions.map { it.manifest })
            val routed = nav.byId.entries.associate { it.key.value to it.value.absolutePath }
            assertEquals(planned, routed.filterKeys { it in planned })
            assertEquals({{PLANNED_SIGN_IN}}, app.policy.signIn?.value)
            val slots = app.slots.filled.entries.associate { (slot, ids) -> slot.value to ids.map { it.value } }
            assertEquals(plannedSlots, slots.filterValues { it.isNotEmpty() })
        } finally {
            app.close()
        }
    }
}
