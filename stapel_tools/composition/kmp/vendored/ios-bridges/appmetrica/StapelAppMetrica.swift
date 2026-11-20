// Stapel iOS bridge: activates AppMetrica once, only when an API key was supplied.
// Source of truth: stapel-kmp ios-bridges/appmetrica/. Shared by the AppMetrica bridges.
// Minimum iOS 15.0. Swift package: appmetrica-sdk-ios (exact version pinned by the scaffold registry).
import Foundation
import AppMetricaCore

enum StapelAppMetrica {
    /// The key from the app's generated secrets; empty means "not configured".
    static var apiKey: String = ""
    private static var activated: Bool?

    /// True when AppMetrica is active. Activation keeps data sending, advertising identifiers and
    /// location off; the Kotlin hub turns data sending on after consent.
    static func activateOnce() -> Bool {
        if let activated { return activated }
        guard !apiKey.isEmpty, let configuration = AppMetricaConfiguration(apiKey: apiKey) else {
            activated = false
            return false
        }
        configuration.dataSendingEnabled = false
        configuration.advertisingIdentifierTrackingEnabled = false
        configuration.locationTracking = false
        if !AppMetrica.isActivated { AppMetrica.activate(with: configuration) }
        activated = true
        return true
    }
}
