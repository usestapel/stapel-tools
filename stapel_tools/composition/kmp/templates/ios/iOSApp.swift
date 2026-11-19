import SwiftUI
import Shared

@main
struct iOSApp: App {
    init() {
        // Launch argument `-stapelOpenUrl <url>`: hands a link to the app as if the system had opened it
        // (the launch smoke uses it; `simctl openurl` stops at the system's "Open in …?" prompt).
        let args = ProcessInfo.processInfo.arguments
        if let i = args.firstIndex(of: "-stapelOpenUrl"), i + 1 < args.count {
            _ = MainViewControllerKt.openUrl(url: args[i + 1])
        }
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}
