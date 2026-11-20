package {{PKG}}

import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import kotlinx.coroutines.launch
import org.koin.core.Koin
import org.koin.core.module.Module
import stapel.app.di.StapelApp
import stapel.notifications.push.di.notificationsPushModule
import stapel.notifications.push.notificationsPushRouting
import stapel.push.AskPolicy
import stapel.push.PushController
import stapel.push.PushOptions
import stapel.push.di.pushModule
import stapel.ui.StapelButton
import stapel.ui.StapelText

/**
 * Push (stapel.composition.json "push"): stapel-push with the notifications brick's device registry
 * and routing; the provider is per platform ([platformProviderModules]). The OS permission is asked
 * {{ASK_DOC}}.
 */
fun appPushModules(): List<Module> = listOf(
    pushModule(PushOptions(ask = AskPolicy.{{ASK_POLICY}}, routing = notificationsPushRouting())),
    notificationsPushModule(),
)

/** A tapped notification opens its deep link through the app's navigator. */
fun attachPush(app: StapelApp) {
    app.koin.getOrNull<PushController>()?.attach { app.openUrl(it) }
}

/** Push state and the switch that turns it on: a deliberate act, never at launch. */
@Composable
fun PushSwitch(koin: Koin) {
    val push = remember(koin) { koin.getOrNull<PushController>() } ?: return
    val state by push.state.collectAsState()
    val scope = rememberCoroutineScope()
    StapelText("Notifications: ${state.permission} / ${state.registration}", Modifier.testTag("app.push.state"))
    if (!state.enabled) StapelButton("Turn on notifications", { scope.launch { push.enable() } }, testTag = "app.push")
}
