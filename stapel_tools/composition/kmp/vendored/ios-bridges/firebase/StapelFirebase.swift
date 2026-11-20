// Stapel iOS bridge: configures Firebase once, only when GoogleService-Info.plist is bundled.
// Source of truth: stapel-kmp ios-bridges/firebase/. Shared by the push and analytics bridges.
// Minimum iOS 15.0. Swift package: firebase-ios-sdk (exact version pinned by the scaffold registry).
import Foundation
import FirebaseCore

enum StapelFirebase {
    private static var configured: Bool?

    /// True when Firebase is configured. Without the plist `FirebaseApp.configure()` raises, so it is
    /// never called then: the providers report "unconfigured" and the app runs without push.
    static func configureOnce() -> Bool {
        if let configured { return configured }
        let present = Bundle.main.path(forResource: "GoogleService-Info", ofType: "plist") != nil
        if present && FirebaseApp.app() == nil {
            FirebaseApp.configure()
        }
        configured = present
        return present
    }
}
