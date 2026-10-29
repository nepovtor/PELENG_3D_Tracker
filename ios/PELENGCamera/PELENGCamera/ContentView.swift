import SwiftUI
import AVFoundation

struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession

    final class PreviewSurface: UIView {
        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
        var previewLayer: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }
    }

    func makeUIView(context: Context) -> PreviewSurface {
        let view = PreviewSurface()
        view.previewLayer.session = session
        view.previewLayer.videoGravity = .resizeAspect
        return view
    }

    func updateUIView(_ uiView: PreviewSurface, context: Context) {}
}

struct ContentView: View {
    @EnvironmentObject private var streamer: CameraStreamer
    @Environment(\.colorScheme) private var colorScheme
    @AppStorage("macAddress") private var macAddress = "192.168.1.2"
    @AppStorage("appearanceMode") private var appearanceMode = "system"
    @State private var pin = ""

    private var isDark: Bool { colorScheme == .dark }
    private var accent: Color { isDark ? Color(red: 0.42, green: 0.88, blue: 0.84) : Color(red: 0.02, green: 0.49, blue: 0.51) }
    private var secondaryText: Color { isDark ? .white.opacity(0.58) : .black.opacity(0.54) }
    private var panelStroke: Color { isDark ? .white.opacity(0.13) : .white.opacity(0.72) }

    var body: some View {
        GeometryReader { geometry in
            let portrait = geometry.size.height >= geometry.size.width
            ZStack {
                atmosphericBackground
                ScrollView(showsIndicators: false) {
                    VStack(alignment: .leading, spacing: 18) {
                        header
                        if portrait { portraitContent }
                        else { landscapeContent(height: geometry.size.height) }
                        safetyNote
                    }
                    .padding(.horizontal, portrait ? 18 : 24)
                    .padding(.vertical, 18)
                }
            }
        }
        .onAppear {
            UIDevice.current.beginGeneratingDeviceOrientationNotifications()
            streamer.prepareCamera()
        }
        .onReceive(NotificationCenter.default.publisher(for: UIDevice.orientationDidChangeNotification)) { _ in
            streamer.deviceOrientationChanged()
        }
    }

    private var atmosphericBackground: some View {
        ZStack {
            LinearGradient(
                colors: isDark
                    ? [Color(red: 0.025, green: 0.04, blue: 0.05), Color(red: 0.04, green: 0.10, blue: 0.12), Color(red: 0.10, green: 0.05, blue: 0.11)]
                    : [Color(red: 0.95, green: 0.88, blue: 0.87), Color(red: 0.86, green: 0.94, blue: 0.96), Color(red: 0.91, green: 0.88, blue: 0.98)],
                startPoint: .topLeading, endPoint: .bottomTrailing
            ).ignoresSafeArea()
            Circle().fill(Color(red: 0.32, green: 0.92, blue: 0.88).opacity(isDark ? 0.20 : 0.48)).frame(width: 360).blur(radius: 78).offset(x: 190, y: -260)
            Circle().fill(Color(red: 1.0, green: 0.57, blue: 0.75).opacity(isDark ? 0.15 : 0.40)).frame(width: 320).blur(radius: 80).offset(x: -180, y: 230)
            Circle().fill(Color(red: 0.48, green: 0.45, blue: 1.0).opacity(isDark ? 0.13 : 0.28)).frame(width: 300).blur(radius: 90).offset(x: 150, y: 420)
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            StereoMark(color: accent).frame(width: 39, height: 31)
            VStack(alignment: .leading, spacing: 1) {
                Text("PELENG").font(.system(size: 17, weight: .bold, design: .rounded)).tracking(1.4)
                Text("REMOTE STEREO SENSOR").font(.system(size: 9, weight: .semibold)).tracking(1.5).foregroundStyle(secondaryText)
            }
            Spacer()
            statusPill
            appearanceMenu
        }
    }

    private var statusPill: some View {
        HStack(spacing: 7) {
            Circle().fill(streamer.isStreaming ? Color.green : Color.orange).frame(width: 7, height: 7)
            Text(streamer.isStreaming ? "В ЭФИРЕ" : "ГОТОВА").font(.system(size: 11, weight: .bold))
        }
        .padding(.horizontal, 11).frame(height: 38)
        .liquidGlass(shape: Capsule(), stroke: panelStroke, accent: accent)
    }

    private var appearanceMenu: some View {
        Menu {
            Button("Как в системе") { appearanceMode = "system" }
            Button("Светлая") { appearanceMode = "light" }
            Button("Тёмная") { appearanceMode = "dark" }
        } label: {
            Image(systemName: isDark ? "moon.fill" : "sun.max.fill").font(.system(size: 14, weight: .semibold)).frame(width: 38, height: 38)
                .liquidGlass(shape: Circle(), stroke: panelStroke, accent: accent)
        }
        .foregroundStyle(isDark ? .white : .black)
        .accessibilityLabel("Оформление")
    }

    private var portraitContent: some View {
        VStack(spacing: 18) {
            cameraCard(maxHeight: 420)
            metricsGrid
            connectionCard
        }
    }

    private func landscapeContent(height: CGFloat) -> some View {
        HStack(alignment: .top, spacing: 18) {
            cameraCard(maxHeight: max(280, height * 0.58))
                .frame(maxWidth: .infinity)
            VStack(spacing: 18) {
                metricsGrid
                connectionCard
            }
            .frame(width: 330)
        }
    }

    private func cameraCard(maxHeight: CGFloat) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("КАМЕРА 02").font(.system(size: 10, weight: .bold)).tracking(1.4).foregroundStyle(accent)
                    Text("iPhone · Rear wide").font(.system(size: 18, weight: .semibold))
                }
                Spacer()
                Text("640 × 480").font(.system(size: 11, weight: .medium, design: .monospaced)).foregroundStyle(secondaryText)
            }
            .padding(16)
            ZStack {
                CameraPreview(session: streamer.session).frame(maxWidth: .infinity, maxHeight: maxHeight).aspectRatio(4/3, contentMode: .fit).background(Color.black)
                Reticle().stroke(accent.opacity(0.72), lineWidth: 1).frame(width: 80, height: 80).allowsHitTesting(false)
                VStack { Spacer(); HStack { Label(streamer.isStreaming ? "Передача в Mac" : "Локальный предпросмотр", systemImage: streamer.isStreaming ? "antenna.radiowaves.left.and.right" : "camera.fill").font(.system(size: 10, weight: .semibold)).padding(.horizontal, 10).frame(height: 30).background(.ultraThinMaterial, in: Capsule()); Spacer() } }.padding(12)
            }
        }
        .glassPanel(stroke: panelStroke, accent: accent)
    }

    private var metricsGrid: some View {
        HStack(spacing: 0) {
            metric("Поток", streamer.isStreaming ? "JPEG / LAN" : "Остановлен")
            Divider().frame(height: 45)
            metric("Передано", "\(streamer.sentFrames)")
            Divider().frame(height: 45)
            metric("Часы ±", streamer.clockUncertaintyMs.map { String(format: "%.1f ms", $0) } ?? "—")
        }
        .padding(.vertical, 14)
        .glassPanel(stroke: panelStroke, accent: accent)
    }

    private func metric(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label).font(.system(size: 10, weight: .medium)).foregroundStyle(secondaryText)
            Text(value).font(.system(size: 13, weight: .semibold, design: .monospaced)).lineLimit(1).minimumScaleFactor(0.7)
        }
        .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 14)
    }

    private var connectionCard: some View {
        VStack(alignment: .leading, spacing: 15) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text("ПОДКЛЮЧЕНИЕ").font(.system(size: 10, weight: .bold)).tracking(1.4).foregroundStyle(accent)
                    Text("MacBook station").font(.system(size: 18, weight: .semibold))
                }
                Spacer()
                Image(systemName: "laptopcomputer.and.iphone").foregroundStyle(accent)
            }
            VStack(spacing: 10) {
                field(title: "IP-адрес Mac", placeholder: "192.168.1.2", text: $macAddress, keyboard: .numbersAndPunctuation)
                field(title: "PIN с экрана Mac", placeholder: "6 цифр", text: $pin, keyboard: .numberPad)
            }
            Button {
                if streamer.isStreaming { streamer.disconnect() }
                else { streamer.connect(address: macAddress, pin: pin) }
            } label: {
                Label(streamer.isStreaming ? "Остановить передачу" : "Подключить и начать", systemImage: streamer.isStreaming ? "stop.fill" : "arrow.up.right")
                    .font(.system(size: 14, weight: .semibold)).frame(maxWidth: .infinity).frame(height: 50)
            }
            .buttonStyle(.plain).foregroundStyle(streamer.isStreaming ? Color.red : (isDark ? .black : .white))
            .background(
                streamer.isStreaming
                    ? LinearGradient(colors: [Color.red.opacity(0.18), Color.red.opacity(0.08)], startPoint: .topLeading, endPoint: .bottomTrailing)
                    : LinearGradient(colors: [accent, Color(red: 0.35, green: 0.88, blue: 0.86)], startPoint: .topLeading, endPoint: .bottomTrailing),
                in: Capsule()
            )
            .overlay(Capsule().stroke(.white.opacity(0.48), lineWidth: 1))
            .shadow(color: accent.opacity(0.25), radius: 16, y: 8)
            Text(streamer.message).font(.system(size: 12)).foregroundStyle(streamer.hasError ? Color.orange : secondaryText).fixedSize(horizontal: false, vertical: true)
        }
        .padding(18).glassPanel(stroke: panelStroke, accent: accent)
    }

    private func field(title: String, placeholder: String, text: Binding<String>, keyboard: UIKeyboardType) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.system(size: 10, weight: .medium)).foregroundStyle(secondaryText)
            TextField(placeholder, text: text)
                .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(keyboard)
                .font(.system(size: 15, design: .monospaced)).padding(.horizontal, 13).frame(height: 46)
                .background(isDark ? Color.white.opacity(0.07) : Color.white.opacity(0.56), in: RoundedRectangle(cornerRadius: 14))
                .overlay(RoundedRectangle(cornerRadius: 14).stroke(panelStroke))
        }
    }

    private var safetyNote: some View {
        Label("Закрепи телефон и MacBook неподвижно. Перемещай только объект или калибровочную доску. Wi‑Fi не даёт аппаратной синхронизации.", systemImage: "info.circle")
            .font(.system(size: 11)).foregroundStyle(secondaryText).fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4)
    }
}

private struct StereoMark: View {
    let color: Color
    var body: some View {
        HStack(spacing: 4) {
            RoundedRectangle(cornerRadius: 8).stroke(color, lineWidth: 2).rotationEffect(.degrees(-6))
            RoundedRectangle(cornerRadius: 8).stroke(color, lineWidth: 2).rotationEffect(.degrees(6))
        }
    }
}

private struct Reticle: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path(); path.addRect(rect.insetBy(dx: 10, dy: 10))
        path.move(to: CGPoint(x: rect.midX, y: rect.minY)); path.addLine(to: CGPoint(x: rect.midX, y: rect.maxY))
        path.move(to: CGPoint(x: rect.minX, y: rect.midY)); path.addLine(to: CGPoint(x: rect.maxX, y: rect.midY)); return path
    }
}

private extension View {
    func glassPanel(stroke: Color, accent: Color) -> some View {
        liquidGlass(shape: RoundedRectangle(cornerRadius: 24, style: .continuous), stroke: stroke, accent: accent)
    }

    func liquidGlass<S: InsettableShape>(shape: S, stroke: Color, accent: Color) -> some View {
        background(.ultraThinMaterial, in: shape)
            .background(
                LinearGradient(
                    colors: [.white.opacity(0.22), accent.opacity(0.06), .clear],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                ),
                in: shape
            )
            .overlay(
                shape.stroke(
                    LinearGradient(
                        colors: [.white.opacity(0.82), accent.opacity(0.42), .white.opacity(0.14)],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    ),
                    lineWidth: 1.2
                )
            )
            .overlay(alignment: .top) {
                shape.inset(by: 2).stroke(.white.opacity(0.22), lineWidth: 1).mask(
                    LinearGradient(colors: [.white, .clear], startPoint: .top, endPoint: .center)
                )
            }
            .shadow(color: accent.opacity(0.12), radius: 20, y: 10)
    }
}
