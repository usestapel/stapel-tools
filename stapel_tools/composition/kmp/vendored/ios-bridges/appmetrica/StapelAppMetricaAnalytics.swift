// Stapel iOS bridge (source of truth: stapel-kmp ios-bridges/appmetrica/; the scaffold copies it into
// the app shell). Implements the Kotlin protocol AppMetricaAnalyticsBridge of stapel-analytics-appmetrica
// (APPMETRICA_ANALYTICS_BRIDGE_ABI = 1) against AppMetricaCore. A transliteration: the Kotlin half
// decides everything. Minimum iOS 15. The app target links AppMetricaCore (no AppMetricaAdSupport).
import Foundation
import AppMetricaCore
import Shared

final class StapelAppMetricaAnalytics: NSObject, AppMetricaAnalyticsBridge {
    static let shared = StapelAppMetricaAnalytics()

    func configured() -> Bool { StapelAppMetrica.activateOnce() }

    func setDataSendingEnabled(enabled: Bool) {
        AppMetrica.setDataSendingEnabled(enabled)
    }

    func reportEvent(name: String, params: [String: String]) {
        AppMetrica.reportEvent(name: name, parameters: params, onFailure: nil)
    }

    func setUserProfileId(id: String?) {
        AppMetrica.userProfileID = id
    }

    func setProfileAttribute(name: String, value: String?) {
        let attribute = ProfileAttribute.customString(name)
        let update = value.map { attribute.withValue($0) } ?? attribute.withValueReset()
        let profile = MutableUserProfile()
        profile.apply(update)
        AppMetrica.reportUserProfile(profile, onFailure: nil)
    }
}
