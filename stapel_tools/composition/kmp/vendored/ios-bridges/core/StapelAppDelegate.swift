// Stapel iOS bridge: the app's one UIApplicationDelegate and UNUserNotificationCenterDelegate.
// Source of truth: stapel-kmp ios-bridges/core/. The scaffold copies it into the app shell unchanged.
// Minimum iOS 15.0.
import UIKit
import UserNotifications

/// What a push bridge answers to the OS callbacks. Each installed push provider's bridge conforms.
protocol StapelNotificationHandling: AnyObject {
    /// APNs handed out a device token.
    func didRegister(deviceToken: Data)
    /// APNs registration failed (a simulator without APNs, no entitlement).
    func didFailToRegister(error: Error)
    /// A notification arrived while the app is on screen; answer how to show it, or nil when it is not this bridge's.
    func willPresent(_ notification: UNNotification) -> UNNotificationPresentationOptions?
    /// The person tapped a notification; answer whether it was this bridge's.
    func didReceive(_ response: UNNotificationResponse) -> Bool
}

/// Installed with `@UIApplicationDelegateAdaptor(StapelAppDelegate.self)`. Swizzling is off for every
/// vendor SDK (`FirebaseAppDelegateProxyEnabled = NO`): every callback is forwarded explicitly here,
/// so two SDKs never fight over one delegate.
final class StapelAppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    /// The push bridges, in order; the app shell sets it before the first screen.
    static var handlers: [StapelNotificationHandling] = []

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        // Before launch finishes, so the tap that launched the app reaches didReceive.
        UNUserNotificationCenter.current().delegate = self
        return true
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        Self.handlers.forEach { $0.didRegister(deviceToken: deviceToken) }
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        Self.handlers.forEach { $0.didFailToRegister(error: error) }
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        for handler in Self.handlers {
            if let options = handler.willPresent(notification) {
                completionHandler(options)
                return
            }
        }
        completionHandler([.banner, .list, .sound])
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        for handler in Self.handlers where handler.didReceive(response) { break }
        completionHandler()
    }
}
