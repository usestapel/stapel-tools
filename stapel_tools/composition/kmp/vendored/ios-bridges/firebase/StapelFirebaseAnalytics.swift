// Stapel iOS bridge (source of truth: stapel-kmp ios-bridges/firebase/; the scaffold copies it into
// the app shell). Implements the Kotlin protocol FirebaseAnalyticsBridge of
// stapel-analytics-firebase (FIREBASE_ANALYTICS_BRIDGE_ABI = 1) against the Firebase Swift SDK.
// The Kotlin side owns every decision (names, limits, consent); this file only transliterates.
// Minimum iOS 15. App target links the FirebaseAnalyticsCore product (no IDFA).
import Foundation
import FirebaseAnalytics
import Shared

final class StapelFirebaseAnalytics: NSObject, FirebaseAnalyticsBridge {
    static let shared = StapelFirebaseAnalytics()

    func configured() -> Bool { StapelFirebase.configureOnce() }

    func setCollectionEnabled(enabled: Bool) {
        Analytics.setAnalyticsCollectionEnabled(enabled)
    }

    func logEvent(name: String, params: [String: String]) {
        Analytics.logEvent(name, parameters: params)
    }

    func logScreen(name: String) {
        Analytics.logEvent(AnalyticsEventScreenView, parameters: [AnalyticsParameterScreenName: name])
    }

    func setUserId(id: String?) {
        Analytics.setUserID(id)
    }

    func setUserProperty(name: String, value: String?) {
        Analytics.setUserProperty(value, forName: name)
    }

    func reset() {
        Analytics.resetAnalyticsData()
    }
}
