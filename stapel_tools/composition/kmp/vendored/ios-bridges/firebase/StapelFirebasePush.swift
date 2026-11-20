// Stapel iOS bridge: Firebase Cloud Messaging for stapel-push-firebase (FIREBASE_PUSH_BRIDGE_ABI 1).
// Source of truth: stapel-kmp ios-bridges/firebase/. The scaffold copies it into the app shell and
// substitutes the framework name in `import Shared`. Minimum iOS 15.0; every API below exists on 15.
// Swift package products: FirebaseMessaging (firebase-ios-sdk, exact version from the scaffold registry).
// Info.plist: FirebaseAppDelegateProxyEnabled = NO (no swizzling: StapelAppDelegate forwards everything),
// FirebaseMessagingAutoInitEnabled = NO (no token before PushController.enable()).
// The FCM registration-token calls (token, deleteToken, didReceiveRegistrationToken) are deprecated
// in firebase-ios-sdk 13 in favour of installation-id registration; the notifications backend
// addresses registration tokens, so this bridge keeps them (compiler warnings, not errors).
import Foundation
import UIKit
import UserNotifications
import FirebaseCore
import FirebaseMessaging
import Shared

/// Implements the Kotlin `FirebasePushBridge`: a transliteration with no decisions of its own; the
/// Kotlin half owns state, retries and threading. Calls arrive on the main thread.
final class StapelFirebasePush: NSObject, FirebasePushBridge, StapelNotificationHandling, MessagingDelegate {
    static let shared = StapelFirebasePush()

    private var listener: FirebasePushListener?
    private var pending: [(FirebasePushListener) -> Void] = []
    private var presentation = "system"
    private var tokenWaiters: [(String?, String?) -> Void] = []

    // MARK: FirebasePushBridge

    func configured() -> Bool {
        guard StapelFirebase.configureOnce() else { return false }
        Messaging.messaging().delegate = self
        return true
    }

    func permission(completion: @escaping (String) -> Void) {
        UNUserNotificationCenter.current().getNotificationSettings { settings in
            let word: String
            switch settings.authorizationStatus {
            case .notDetermined: word = "notDetermined"
            case .authorized, .ephemeral: word = "granted"
            case .provisional: word = "provisional"
            default: word = "denied"
            }
            DispatchQueue.main.async { completion(word) }
        }
    }

    func requestPermission(provisional: Bool, completion: @escaping (String) -> Void) {
        var options: UNAuthorizationOptions = [.alert, .badge, .sound]
        if provisional { options.insert(.provisional) }
        UNUserNotificationCenter.current().requestAuthorization(options: options) { _, _ in
            DispatchQueue.main.async { self.permission(completion: completion) }
        }
    }

    func token(completion: @escaping (String?, String?) -> Void) {
        // FCM needs the APNs token first; registration is idempotent and answers in StapelAppDelegate.
        if Messaging.messaging().apnsToken == nil {
            tokenWaiters.append(completion)
            if tokenWaiters.count == 1 { UIApplication.shared.registerForRemoteNotifications() }
            return
        }
        fetchToken(completion)
    }

    func deleteToken(completion: @escaping (String?) -> Void) {
        Messaging.messaging().deleteToken { error in
            DispatchQueue.main.async { completion(error.map { ($0 as NSError).localizedDescription }) }
        }
    }

    func setListener(listener: FirebasePushListener?) {
        self.listener = listener
        guard let listener else { return }
        let events = pending
        pending.removeAll()
        events.forEach { $0(listener) }
    }

    func setForegroundPresentation(mode: String) {
        presentation = mode
    }

    func ensureChannel(id: String, name: String, importance: String) {
        // Android only.
    }

    // MARK: StapelNotificationHandling

    func didRegister(deviceToken: Data) {
        guard StapelFirebase.configureOnce() else { return }
        Messaging.messaging().apnsToken = deviceToken
        let waiting = tokenWaiters
        tokenWaiters.removeAll()
        waiting.forEach { fetchToken($0) }
    }

    func didFailToRegister(error: Error) {
        let waiting = tokenWaiters
        tokenWaiters.removeAll()
        let text = (error as NSError).localizedDescription
        waiting.forEach { $0(nil, text) }
    }

    func willPresent(_ notification: UNNotification) -> UNNotificationPresentationOptions? {
        let content = notification.request.content
        let (id, data) = Self.payload(content.userInfo)
        deliver { $0.onMessage(id: id, title: content.title, body: content.body, data: data, foreground: true) }
        switch presentation {
        case "system": return [.banner, .list, .sound]
        default: return []
        }
    }

    func didReceive(_ response: UNNotificationResponse) -> Bool {
        let content = response.notification.request.content
        let (id, data) = Self.payload(content.userInfo)
        let action = response.actionIdentifier == UNNotificationDefaultActionIdentifier ? nil : response.actionIdentifier
        deliver { $0.onOpened(id: id, title: content.title, body: content.body, data: data, actionId: action) }
        return true
    }

    // MARK: MessagingDelegate

    func messaging(_ messaging: Messaging, didReceiveRegistrationToken fcmToken: String?) {
        guard let fcmToken else { return }
        DispatchQueue.main.async { self.deliver { $0.onToken(token: fcmToken) } }
    }

    // MARK: private

    private func fetchToken(_ completion: @escaping (String?, String?) -> Void) {
        Messaging.messaging().token { token, error in
            DispatchQueue.main.async { completion(token, error.map { ($0 as NSError).localizedDescription }) }
        }
    }

    private func deliver(_ event: @escaping (FirebasePushListener) -> Void) {
        if let listener { event(listener) } else { pending.append(event) }
    }

    /// The backend's `data` map: every top-level string except `aps` and the SDK's own keys.
    private static func payload(_ userInfo: [AnyHashable: Any]) -> (String?, [String: String]) {
        var data: [String: String] = [:]
        for (key, value) in userInfo {
            guard let key = key as? String, let value = value as? String else { continue }
            if key == "aps" || key.hasPrefix("gcm.") || key.hasPrefix("google.") || key == "fcm_options" { continue }
            data[key] = value
        }
        return (userInfo["gcm.message_id"] as? String, data)
    }
}
