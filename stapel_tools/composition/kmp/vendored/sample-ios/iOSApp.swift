import SwiftUI
import Shared

@main
struct iOSApp: App {
    init() {
        let args = ProcessInfo.processInfo.arguments
        // Launch argument `-stapelLogFile <name>`: the log records also go to Documents/<name> (the launch
        // smoke reads its markers from the app's data container, independent of unified-log filtering).
        if let i = args.firstIndex(of: "-stapelLogFile"), i + 1 < args.count {
            MainViewControllerKt.useLogFile(name: args[i + 1])
        }
        // Launch argument `-stapelBaseUrl <url>`: another backend (UI tests run a stand on their own port).
        if let i = args.firstIndex(of: "-stapelBaseUrl"), i + 1 < args.count {
            MainViewControllerKt.useBaseUrl(url: args[i + 1])
        }
        // Launch argument `-stapelOpenUrl <url>`: hands a link to the app as if the system had opened it
        // (the launch smoke uses it; `simctl openurl` stops at the system's "Open in …?" prompt).
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
