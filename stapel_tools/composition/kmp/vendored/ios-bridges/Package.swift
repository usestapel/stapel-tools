// swift-tools-version:6.2
// The compile check of the Stapel iOS bridges (CI job `ios-bridges`): each bridge directory is a
// target that must compile against the vendor's Swift package at the exact version the scaffold
// pins, and against the Kotlin protocols as a Kotlin framework named `Shared` exports them
// (check/: a separate Gradle build consuming the provider artifacts by coordinates).
//
//   ./gradlew publishAllPublicationsToStagingRepository            # repository root
//   ./gradlew -p ios-bridges/check assembleSharedReleaseXCFramework
//   (cd ios-bridges && xcodebuild -scheme StapelBridges -destination 'generic/platform=iOS Simulator' build)
import PackageDescription

let package = Package(
    name: "StapelBridges",
    platforms: [.iOS(.v15)],
    products: [
        .library(name: "StapelBridges", targets: ["StapelBridges"]),
    ],
    dependencies: [
        .package(url: "https://github.com/firebase/firebase-ios-sdk", exact: "13.2.0"),
        .package(url: "https://github.com/appmetrica/appmetrica-sdk-ios", exact: "7.0.0"),
    ],
    targets: [
        .binaryTarget(name: "Shared", path: "check/shared/build/XCFrameworks/release/Shared.xcframework"),
        // One target over every bridge directory: the app shell compiles them together the same way
        // (the delegate in core/ talks to the push bridges).
        .target(
            name: "StapelBridges",
            dependencies: [
                "Shared",
                .product(name: "FirebaseCore", package: "firebase-ios-sdk"),
                .product(name: "FirebaseAnalyticsCore", package: "firebase-ios-sdk"),
                .product(name: "FirebaseMessaging", package: "firebase-ios-sdk"),
                .product(name: "AppMetricaCore", package: "appmetrica-sdk-ios"),
            ],
            path: ".",
            exclude: ["check", "README.md", "build"],
            sources: ["core", "firebase", "appmetrica"]
        ),
    ],
    // The app shell compiles these files in Swift 5 mode (SWIFT_VERSION = 5.0 in the templated project).
    swiftLanguageModes: [.v5]
)
