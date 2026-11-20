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
        val f = out.get().asFile.resolve("shop/generated/AppEnvironment.kt")
        f.parentFile.mkdirs()
        f.writeText(
            "// Generated from stapel.environments.json (environment '$name'). Do not edit.\npackage shop.generated\n\n" +
                "internal const val APP_ENVIRONMENT: String = \"$name\"\n" +
                "internal val APP_POSTURE: stapel.config.Posture = stapel.config.Posture.$posture\n" +
                "internal val APP_BASE_URLS: Map<String, String> = mapOf(" +
                urls.entries.sortedBy { it.key }.joinToString(", ") { "\"${it.key}\" to \"${it.value}\"" } + ")\n",
        )
    }
}

// --- the profiles walk against a stand with auth + profiles + cdn: only with -Pstapel.walk.profilesBaseUrl=<url> ---
val profilesWalkBaseUrl = providers.gradleProperty("stapel.walk.profilesBaseUrl")
val generateProfilesWalkConfig = tasks.register("generateProfilesWalkConfig") {
    val url = providers.gradleProperty("stapel.walk.profilesBaseUrl")
    val out = layout.buildDirectory.dir("generated/profilesWalkConfig")
    inputs.property("baseUrl", url.orElse(""))
    outputs.dir(out)
    doLast {
        val f = out.get().asFile.resolve("shop/ProfilesWalkConfig.kt")
        f.parentFile.mkdirs()
        f.writeText("package shop\n\ninternal const val PROFILES_WALK_BASE_URL: String = \"${url.get()}\"\n")
    }
}

// --- the sign-in walk against a real backend: only with -Pstapel.walk.baseUrl=<url> ---
val walkBaseUrl = providers.gradleProperty("stapel.walk.baseUrl")
val generateWalkConfig = tasks.register("generateWalkConfig") {
    val walkBaseUrl = providers.gradleProperty("stapel.walk.baseUrl")
    val out = layout.buildDirectory.dir("generated/walkConfig")
    inputs.property("baseUrl", walkBaseUrl.orElse(""))
    outputs.dir(out)
    doLast {
        val f = out.get().asFile.resolve("shop/WalkConfig.kt")
        f.parentFile.mkdirs()
        f.writeText("package shop\n\ninternal const val WALK_BASE_URL: String = \"${walkBaseUrl.get()}\"\n")
    }
}

kotlin {
    jvm()
    android {
        namespace = "shop.shared"
        compileSdk = libs.versions.android.compileSdk.get().toInt()
        minSdk = libs.versions.android.minSdk.get().toInt()
        compilerOptions { jvmTarget = JvmTarget.JVM_17 }
        androidResources { enable = true }
    }
    listOf(iosArm64(), iosSimulatorArm64()).forEach { target ->
        target.binaries.framework {
            baseName = "Shared"
            isStatic = true
        }
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
            dependencies {
                api(project.dependencies.platform(libs.stapel.bom))
                api(libs.stapel.app)
                implementation(libs.stapel.auth.ui)
                implementation(libs.stapel.profiles.ui)
                implementation(libs.stapel.ui.material3)
                implementation(libs.stapel.wire)
                implementation(libs.koin.core)
            }
        }
        commonTest {
            if (walkBaseUrl.isPresent) {
                kotlin.srcDir("src/walkTest/kotlin")
                kotlin.srcDir(generateWalkConfig)
            }
            if (profilesWalkBaseUrl.isPresent) {
                kotlin.srcDir("src/profilesWalkTest/kotlin")
                kotlin.srcDir(generateProfilesWalkConfig)
            }
            dependencies {
                implementation(libs.kotlin.test)
                implementation(libs.stapel.auth.testing)
                implementation(libs.stapel.profiles.testing)
                implementation(libs.compose.ui.test)
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
    packageName.set("shop.theme")
    objectName.set("AppTokens")
}

stapelCodegen {
    module("orders") {
        contractDir.set(rootProject.layout.projectDirectory.dir("contracts/orders"))
        packageName.set("shop.orders.api")
    }
}

// -Pstapel.ios.simulator=<udid>: the simulator the iOS tests run on (the oldest-runtime CI job picks one).
providers.gradleProperty("stapel.ios.simulator").orNull?.let { udid ->
    tasks.withType<org.jetbrains.kotlin.gradle.targets.native.tasks.KotlinNativeSimulatorTest>().configureEach { device.set(udid) }
}

tasks.withType<Test>().configureEach {
    testLogging { events("passed", "failed", "skipped"); exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL }
}
