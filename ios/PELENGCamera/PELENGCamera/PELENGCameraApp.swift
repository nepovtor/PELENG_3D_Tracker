import SwiftUI

@main
struct PELENGCameraApp: App {
    @StateObject private var camera = CameraStreamer()
    @AppStorage("appearanceMode") private var appearanceMode = "system"

    private var preferredScheme: ColorScheme? {
        switch appearanceMode {
        case "light": return .light
        case "dark": return .dark
        default: return nil
        }
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(camera)
                .preferredColorScheme(preferredScheme)
        }
    }
}
