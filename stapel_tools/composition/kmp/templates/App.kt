package {{PKG}}

import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import kotlinx.serialization.Serializable
import {{PKG}}.generated.APP_BASE_URLS
import {{PKG}}.generated.APP_ENVIRONMENT
import {{PKG}}.generated.APP_POSTURE
import {{PKG}}.generated.appClientsModule
import {{PKG}}.theme.AppTokens
{{IMPORTS}}import stapel.app.di.BrickContext
import stapel.app.di.StapelApp
import stapel.app.di.StapelAppBuilder
import stapel.app.di.StapelAppContent
import stapel.app.di.stapelApp
import stapel.config.StapelEnvironment
import stapel.config.platformClientInfo
import stapel.navigation.DeepLinkConfig
import stapel.navigation.DestinationId
import stapel.navigation.NavIcons
import stapel.navigation.PathCodec
import stapel.navigation.Route
import stapel.navigation.compose.NavContribution
import stapel.navigation.compose.navContribution
import stapel.runtime.PlatformContext
import stapel.ui.StapelPage
import stapel.ui.StapelText
import stapel.ui.material3.StapelMaterialTheme

/** The app's own home, `/app/home`. */
@Serializable
data object HomeRoute : Route

/**
 * The whole app, assembled once per process (stapel.composition.json is what the scaffold planned).
 *
 * @param target `android` | `ios` | `desktop`: picks the base URL of the environment.
 * @param baseUrl overrides the environment's base URL (tests, a stand on another port).
{{DATA_DIR_DOC}} */
fun createApp(
    platform: PlatformContext,
    target: String,
    baseUrl: String? = null,
{{DATA_DIR_PARAM}}    configure: StapelAppBuilder.() -> Unit = {},
): StapelApp = stapelApp(platform) {
    environment(
        StapelEnvironment.single(
            name = APP_ENVIRONMENT,
            baseUrl = baseUrl ?: APP_BASE_URLS.getValue(target),
            posture = APP_POSTURE,
            client = platformClientInfo(appId = "{{APP_ID}}", appVersion = "0.1.0", build = "1"),
        ),
    )
{{OFFLINE_LINE}}{{BRICKS_LINE}}    screens(::appScreens)
    modules(appClientsModule())
    navigation(
        home = DestinationId("{{HOME_ID}}"),
        deepLinks = DeepLinkConfig(schemes = setOf("{{SCHEME}}"), hosts = setOf({{HOSTS}})),
    )
    theme { content -> StapelMaterialTheme(tokens = AppTokens, content = content) }
    configure()
}

/** The app UI. */
@Composable
fun App(app: StapelApp) {
    StapelAppContent(app)
}

/** The app's own destinations. Add screens here; ids and paths follow the web's (`/app/<path>`). */
fun appScreens(context: BrickContext): NavContribution = navContribution("app:{{SLUG}}", "0.1.0") {
    destination<HomeRoute>(
        id = "{{HOME_ID}}", path = "home", labelKey = "app.nav.home", icon = NavIcons.Home,
        codec = PathCodec.constant(HomeRoute), requiresAuth = {{HOME_REQUIRES_AUTH}}, order = 0,
    ) { HomeScreen() }
}

@Composable
private fun HomeScreen() {
    StapelPage(Modifier.testTag("{{HOME_ID}}")) {
        StapelText({{TITLE_KT_STRING}})
    }
}
