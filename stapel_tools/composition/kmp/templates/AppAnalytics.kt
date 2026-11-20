package {{PKG}}

import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import org.koin.core.Koin
import org.koin.core.module.Module
import org.koin.dsl.module
import stapel.analytics.AnalyticsControl
import stapel.analytics.AnalyticsOptions
import stapel.analytics.AnalyticsSink
import stapel.analytics.Consent
import stapel.analytics.ConsentDefault
import stapel.analytics.ConsentState
import stapel.analytics.LogSink
import stapel.analytics.di.analyticsModule
import stapel.analytics.sha256Hex
{{ANALYTICS_IMPORTS}}import stapel.app.di.StapelApp
import stapel.di.StapelQualifiers
import stapel.di.contribute
import stapel.runtime.Analytics
import stapel.session.SessionManager
import stapel.session.SessionState
import stapel.ui.StapelButton
import stapel.ui.StapelText

/**
 * The app's analytics (stapel.composition.json "analytics"): the stapel-analytics hub with the
 * consent default the scaffold was given and the bricks' generated event registries, the log sink
 * (`stapel.analytics event <kind> <name>` markers) and the selected providers. The vendor sinks are
 * per platform: [platformProviderModules].
 *
 * @param target `android` | `ios` | `desktop`: the batch source of the first-party sink.
 */
fun appAnalyticsModules(target: String): List<Module> = listOf(
    analyticsModule(AnalyticsOptions(consentDefault = ConsentDefault.{{CONSENT_DEFAULT}}, registry = {{REGISTRY}})),
    module { contribute<AnalyticsSink>("analytics.log") { LogSink(get()) } },
{{COLLECTOR_LINE}})

/**
 * Identifies the signed-in account to analytics: `identify(sha256Hex(accountId))` on sign-in,
 * `identify(null)` (every sink resets) when the session ends. The hub never sees the raw id.
 */
fun bindAnalyticsIdentity(app: StapelApp) {
    val analytics = app.koin.getOrNull<Analytics>() ?: return
    val sessions = app.koin.getOrNull<SessionManager>() ?: return
    app.koin.get<CoroutineScope>(StapelQualifiers.AppScope).launch {
        var last: String? = null
        sessions.state.collect { s ->
            val account = (s as? SessionState.Authenticated)?.accountId
            if (s is SessionState.Initializing || account == last) return@collect
            last = account
            analytics.identify(account?.let(::sha256Hex))
        }
    }
}

/**
 * The consent sheet, shown while the answer is pending: two buttons, the choice persisted per
 * installation by the hub. Nothing leaves the device before "Allow". Replace it with the project's
 * own consent UI when it has one.
 */
@Composable
fun AnalyticsConsentSheet(koin: Koin) {
    val control = remember(koin) { koin.getOrNull<AnalyticsControl>() } ?: return
    val consent by control.consent.collectAsState()
    if (consent.analytics != ConsentState.Pending) return
    val scope = rememberCoroutineScope()
    StapelText("Help us improve the app with anonymous usage analytics?", Modifier.testTag("app.consent"))
    StapelButton("Allow", { scope.launch { control.setConsent(Consent(ConsentState.Granted)) } }, testTag = "app.consent.allow")
    StapelButton("Decline", { scope.launch { control.setConsent(Consent(ConsentState.Denied)) } }, testTag = "app.consent.decline")
}
