// -Pshop.firebase=true (shared/build.gradle.kts) adds Google Analytics for Firebase; its config file
// androidApp/google-services.json is a gitignored deploy input (androidApp/fixtures/ holds a fake one).
val shopFirebase = providers.gradleProperty("shop.firebase").orNull == "true"
buildscript {
    if (providers.gradleProperty("shop.firebase").orNull == "true") {
        repositories { google(); mavenCentral() }
        dependencies { classpath("com.google.gms:google-services:4.6.0") }
    }
}

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.compose.compiler)
}

kotlin {
    compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) }
}

dependencies {
    implementation(project(":shared"))
    implementation(libs.androidx.activity.compose)
}

android {
    namespace = "shop.android"
    compileSdk = libs.versions.android.compileSdk.get().toInt()
    defaultConfig {
        applicationId = "io.github.usestapel.sample.shop"
        minSdk = libs.versions.android.minSdk.get().toInt()
        targetSdk = libs.versions.android.compileSdk.get().toInt()
        versionCode = 1
        versionName = "0.1.0"
    }
    packaging { resources { excludes += "/META-INF/{AL2.0,LGPL2.1}" } }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures { compose = true }
}

// Ruling K8: without the config file the provider is a no-op that says so (`stapel.analytics unavailable
// firebase unconfigured google-services.json`); a release build refuses by name instead of shipping it.
val firebaseConfig = layout.projectDirectory.file("google-services.json").asFile
if (shopFirebase && firebaseConfig.exists()) apply(plugin = "com.google.gms.google-services")
if (shopFirebase) {
    tasks.matching { it.name == "preReleaseBuild" }.configureEach {
        val config = firebaseConfig
        doFirst {
            if (!config.exists()) {
                throw GradleException(
                    "shop.firebase: a release build without androidApp/google-services.json would ship Firebase Analytics " +
                        "dead. Put the project's file there (gitignored) or build without -Pshop.firebase.",
                )
            }
        }
    }
}
