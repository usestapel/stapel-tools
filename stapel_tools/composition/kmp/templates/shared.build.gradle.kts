import groovy.json.JsonSlurper
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.kotlin.multiplatform)
    alias(libs.plugins.android.kotlin.multiplatform.library)
    alias(libs.plugins.compose.multiplatform)
    alias(libs.plugins.compose.compiler)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.stapel.codegen)
    alias(libs.plugins.stapel.tokens)
}

// The minimum iOS version of the app (Kotlin/Native binaries and iosApp's IPHONEOS_DEPLOYMENT_TARGET).
val IOS_FLOOR = "15.0"

// The project's own backend modules (stapel.composition.json "custom"). Each one gets a generated
// client once its contract is under contracts/<module>/ (`make contracts` after the backend has
// endpoints); until then it is listed and skipped, never half-wired.
val customModules: List<String> = listOf({{CUSTOM_MODULES}})
val contractsDir = rootProject.layout.projectDirectory.dir("contracts")
val presentModules: List<String> = customModules.filter { contractsDir.file("$it/schema.json").asFile.isFile }

fun pascal(text: String): String = text.split(Regex("[^A-Za-z0-9]+")).filter { it.isNotEmpty() }
    .flatMap { it.split(Regex("(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")) }
    .filter { it.isNotEmpty() }.joinToString("") { it.replaceFirstChar(Char::uppercaseChar) }

fun apiPackage(module: String): String = "{{PKG}}.api." + module.lowercase().replace(Regex("[^a-z0-9]+"), "_")

// --- the app's environment: stapel.environments.json -> generated AppEnvironment.kt (-Pstapel.env=<name>) ---
val generateAppEnvironment = tasks.register("generateAppEnvironment") {
    val environmentsFile = rootProject.layout.projectDirectory.file("stapel.environments.json")
    val envName = providers.gradleProperty("stapel.env")
    val out = layout.buildDirectory.dir("generated/appEnvironment")
    inputs.file(environmentsFile)
    inputs.property("env", envName.orElse(""))
    outputs.dir(out)
    doLast {
        @Suppress("UNCHECKED_CAST")
        val doc = JsonSlurper().parse(environmentsFile.asFile) as Map<String, Any?>
        val name = envName.orNull ?: doc["default"] as String
        @Suppress("UNCHECKED_CAST")
        val env = (doc["environments"] as Map<String, Map<String, Any?>>)[name] ?: error("stapel.environments.json has no '$name'")
        val urls = when (val b = env["baseUrl"]) {
            is String -> mapOf("android" to b, "ios" to b, "desktop" to b)
            else -> @Suppress("UNCHECKED_CAST") (b as Map<String, String>)
        }
        val posture = if (env["posture"] == "live") "Live" else "Prototype"
        val f = out.get().asFile.resolve("{{PKG_PATH}}/generated/AppEnvironment.kt")
        f.parentFile.mkdirs()
        f.writeText(
            "// Generated from stapel.environments.json (environment '$name'). Do not edit.\npackage {{PKG}}.generated\n\n" +
                "internal const val APP_ENVIRONMENT: String = \"$name\"\n" +
                "internal val APP_POSTURE: stapel.config.Posture = stapel.config.Posture.$posture\n" +
                "internal val APP_BASE_URLS: Map<String, String> = mapOf(" +
                urls.entries.sortedBy { it.key }.joinToString(", ") { "\"${it.key}\" to \"${it.value}\"" } + ")\n",
        )
    }
}

// --- the Koin bindings of the generated clients of the project's own modules -> AppClients.kt ---
val appClientBindings: String = presentModules.joinToString("") { m ->
    val api = apiPackage(m) + "." + pascal(m)
    "    single<${api}Api> { ${api}ApiClient(get<stapel.wire.ApiTransport>()) }\n"
}
val generateAppClients = tasks.register("generateAppClients") {
    val bindings = appClientBindings
    val out = layout.buildDirectory.dir("generated/appClients")
    inputs.property("bindings", bindings)
    outputs.dir(out)
    doLast {
        val f = out.get().asFile.resolve("{{PKG_PATH}}/generated/AppClients.kt")
        f.parentFile.mkdirs()
        f.writeText(
            "// Generated from the contracts under contracts/ (stapelCodegen). Do not edit.\npackage {{PKG}}.generated\n\n" +
                "/** One Koin module binding the client of every project-owned backend module with a contract. */\n" +
                "internal fun appClientsModule(): org.koin.core.module.Module = org.koin.dsl.module {\n" + bindings + "}\n",
        )
    }
}

{{SECRETS_TASK}}// --- the sign-in walk against a real backend: only with -Pstapel.walk.baseUrl=<url> ---
val walkBaseUrl = providers.gradleProperty("stapel.walk.baseUrl")
val generateWalkConfig = tasks.register("generateWalkConfig") {
    val walkBaseUrl = providers.gradleProperty("stapel.walk.baseUrl")
    val out = layout.buildDirectory.dir("generated/walkConfig")
    inputs.property("baseUrl", walkBaseUrl.orElse(""))
    outputs.dir(out)
    doLast {
        val f = out.get().asFile.resolve("{{PKG_PATH}}/WalkConfig.kt")
        f.parentFile.mkdirs()
        f.writeText("package {{PKG}}\n\ninternal const val WALK_BASE_URL: String = \"${walkBaseUrl.get()}\"\n")
    }
}

kotlin {
    android {
        namespace = "{{NAMESPACE}}.shared"
        compileSdk = libs.versions.android.compileSdk.get().toInt()
        minSdk = libs.versions.android.minSdk.get().toInt()
        compilerOptions { jvmTarget = JvmTarget.JVM_17 }
        androidResources { enable = true }
    }
    listOf(iosArm64(), iosSimulatorArm64()).forEach { target ->
        target.binaries.framework {
            baseName = "Shared"
            isStatic = true
{{FRAMEWORK_EXPORTS}}        }
        // The iOS floor (15.0): Kotlin/Native's minimum OS for every binary of this target, equal to the
        // Xcode project's IPHONEOS_DEPLOYMENT_TARGET. Kotlin has no DSL for it; the documented route is
        // the konan property override.
        target.binaries.configureEach {
            freeCompilerArgs += "-Xoverride-konan-properties=osVersionMin.ios_arm64=$IOS_FLOOR;osVersionMin.ios_simulator_arm64=$IOS_FLOOR"
        }
    }
    jvm { compilerOptions { jvmTarget = JvmTarget.JVM_17 } }

    sourceSets {
        commonMain {
            kotlin.srcDir(generateAppEnvironment)
            kotlin.srcDir(generateAppClients)
{{SECRETS_SRC}}            dependencies {
                api(project.dependencies.platform(libs.stapel.bom))
                api(libs.stapel.app)
{{BRICK_DEPS}}{{FEATURE_DEPS}}                implementation(libs.stapel.ui.material3)
                implementation(libs.stapel.wire)
{{OFFLINE_DEP}}                implementation(libs.koin.core)
            }
        }
{{IOS_MAIN_DEPS}}        commonTest {
            if (walkBaseUrl.isPresent) {
                kotlin.srcDir("src/walkTest/kotlin")
                kotlin.srcDir(generateWalkConfig)
            }
            dependencies {
                implementation(libs.kotlin.test)
{{TESTING_DEPS}}                implementation(libs.compose.ui.test)
            }
        }
        jvmTest.dependencies {
            implementation(compose.desktop.currentOs)
            implementation(libs.kotlinx.coroutines.swing)
        }
    }
}

stapelTokens {
    theme.set(rootProject.layout.projectDirectory.file("stapel.theme.json"))
    packageName.set("{{PKG}}.theme")
    objectName.set("AppTokens")
}

stapelCodegen {
    presentModules.forEach { m ->
        module(m) {
            contractDir.set(contractsDir.dir(m))
            packageName.set(apiPackage(m))
        }
    }
}

// checkStapelTokens compares against generateStapelTokens' output but declares no dependency on it
// (stapel-kmp tokens plugin); without this line a clean build reports the tokens "stale".
tasks.named("checkStapelTokens") { dependsOn("generateStapelTokens") }

tasks.withType<Test>().configureEach {
    testLogging { events("passed", "failed", "skipped"); exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL }
}
