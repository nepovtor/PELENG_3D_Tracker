import Foundation
import AVFoundation
import CoreImage
import SwiftUI

/// Native rear-camera capture and authenticated streaming over the user's local network.
/// Clock samples and captures use ProcessInfo.systemUptime in the same iPhone time domain.
final class CameraStreamer: NSObject, ObservableObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    @Published private(set) var isStreaming = false
    @Published private(set) var sentFrames = 0
    @Published private(set) var clockUncertaintyMs: Double? = nil
    @Published private(set) var message = "Подготовка камеры"
    @Published private(set) var hasError = false

    let session = AVCaptureSession()
    private let captureQueue = DispatchQueue(label: "org.peleng.capture.setup")
    private let frameQueue = DispatchQueue(label: "org.peleng.capture.frames")
    private let imageContext = CIContext(options: [.useSoftwareRenderer: false])
    private let lock = NSLock()
    private let deviceId: String
    private let cameraModeBase = "back-wide-landscape-640x480-v1"
    private var activeCameraMode = ""
    private var activeOrientation = ""
    private var cameraReady = false
    private var endpoint: URL?
    private var token: String?
    private var offset = 0.0
    private var uncertainty = Double.infinity
    private var lastSync = 0.0
    private var lastFrameSent = 0.0
    private var frameId = 0
    private var pending = 0
    private var failures = 0
    private var syncTask: Task<Void, Never>?
    private var active = false

    private let http: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 3
        config.timeoutIntervalForResource = 7
        config.waitsForConnectivity = false
        config.allowsCellularAccess = false
        return URLSession(configuration: config)
    }()

    override init() {
        if let known = UserDefaults.standard.string(forKey: "pelengDeviceID") { deviceId = known }
        else {
            let fresh = UUID().uuidString
            UserDefaults.standard.set(fresh, forKey: "pelengDeviceID")
            deviceId = fresh
        }
        super.init()
    }

    func prepareCamera() {
        AVCaptureDevice.requestAccess(for: .video) { [weak self] allowed in
            guard let self else { return }
            if !allowed {
                self.show("Разреши доступ к камере в Настройках iPhone", error: true)
                return
            }
            self.captureQueue.async { self.configureCapture() }
        }
    }

    private func configureCapture() {
        guard !cameraReady else { return }
        guard let camera = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back) else {
            show("Задняя широкоугольная камера не найдена", error: true)
            return
        }
        do {
            let input = try AVCaptureDeviceInput(device: camera)
            session.beginConfiguration()
            session.sessionPreset = .vga640x480
            guard session.canAddInput(input) else {
                session.commitConfiguration()
                show("Невозможно подключить камеру", error: true)
                return
            }
            session.addInput(input)
            let output = AVCaptureVideoDataOutput()
            output.alwaysDiscardsLateVideoFrames = true
            output.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
            guard session.canAddOutput(output) else {
                session.commitConfiguration()
                show("Не удалось настроить видеовыход", error: true)
                return
            }
            session.addOutput(output)
            output.setSampleBufferDelegate(self, queue: frameQueue)
            if let connection = output.connection(with: .video), connection.isVideoOrientationSupported {
                connection.videoOrientation = .landscapeRight
            }
            session.commitConfiguration()
            cameraReady = true
            session.startRunning()
            show("Камера активна. Введи адрес Mac и PIN.", error: false)
        } catch {
            show("Ошибка камеры: \(error.localizedDescription)", error: true)
        }
    }

    func connect(address: String, pin: String) {
        guard let orientation = orientationKey(UIDevice.current.orientation) else {
            show("Держи iPhone вертикально или горизонтально перед подключением", error: true)
            return
        }
        let cameraMode = "\(cameraModeBase)-\(orientation)"
        let host = address.trimmingCharacters(in: .whitespacesAndNewlines)
        let addr = host.contains("://") ? host : "http://\(host)\(host.contains(":") ? "" : ":8765")"
        guard let url = URL(string: addr), url.scheme == "http", let hostname = url.host,
              !hostname.isEmpty, pin.count == 6, pin.allSatisfy({ $0.isNumber }) else {
            show("Введи IP Mac (например, 192.168.1.2) и шестизначный PIN", error: true)
            return
        }
        // Explicitly limit transport to a user's LAN IP or .local host.
        guard isPrivateHost(hostname) else {
            show("Разрешён только локальный IP Mac или адрес .local", error: true)
            return
        }
        let base = url.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard let server = URL(string: base) else { return }
        show("Сопряжение с Mac…", error: false)
        Task {
            do {
                var request = URLRequest(url: server.appendingPathComponent("api/pair"))
                request.httpMethod = "POST"
                request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                request.httpBody = try JSONSerialization.data(withJSONObject: ["pin":pin, "device_id":deviceId, "camera_mode":cameraMode])
                let (data, response) = try await http.data(for: request)
                guard let r = response as? HTTPURLResponse, r.statusCode == 200,
                      let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                      let newToken = json["token"] as? String else {
                    throw NSError(domain: "PELENG", code: 401, userInfo: [NSLocalizedDescriptionKey: "Mac отклонил сопряжение. Проверь IP и PIN."])
                }
                lock.lock()
                endpoint = server
                token = newToken
                activeCameraMode = cameraMode
                activeOrientation = orientation
                active = true
                frameId = 0
                failures = 0
                lastSync = 0
                lock.unlock()
                let success = await synchronizeClock()
                if success {
                    await MainActor.run { self.isStreaming = true; self.sentFrames = 0; self.message = "Передача живых кадров на Mac"; self.hasError = false }
                    startSyncLoop()
                } else {
                    disconnect()
                    show("Не удалось синхронизировать часы. Проверь Wi-Fi.", error: true)
                }
            } catch {
                show("Подключение: \(error.localizedDescription)", error: true)
            }
        }
    }

    private func isPrivateHost(_ host: String) -> Bool {
        if host == "localhost" || host.hasSuffix(".local") { return true }
        let parts = host.split(separator: ".").compactMap { UInt8($0) }
        guard parts.count == 4 else { return false }
        return parts[0] == 10 || parts[0] == 192 && parts[1] == 168 ||
            parts[0] == 172 && (16...31).contains(parts[1]) ||
            parts[0] == 169 && parts[1] == 254
    }

    private func orientationKey(_ orientation: UIDeviceOrientation) -> String? {
        switch orientation {
        case .portrait: return "portrait"
        case .portraitUpsideDown: return "portrait-upside-down"
        case .landscapeLeft: return "landscape-left"
        case .landscapeRight: return "landscape-right"
        default: return nil
        }
    }

    func deviceOrientationChanged() {
        guard let orientation = orientationKey(UIDevice.current.orientation) else { return }
        lock.lock()
        let changed = active && !activeOrientation.isEmpty && orientation != activeOrientation
        if changed {
            active = false
            token = nil
            endpoint = nil
            pending = 0
            activeCameraMode = ""
            activeOrientation = ""
        }
        lock.unlock()
        guard changed else { return }
        syncTask?.cancel()
        syncTask = nil
        DispatchQueue.main.async {
            self.isStreaming = false
            self.clockUncertaintyMs = nil
            self.message = "Ориентация изменена. Подключись заново и повтори калибровку."
            self.hasError = true
        }
    }

    private func startSyncLoop() {
        syncTask?.cancel()
        syncTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 4_000_000_000)
                if Task.isCancelled { break }
                _ = await self?.synchronizeClock()
            }
        }
    }

    private func synchronizeClock() async -> Bool {
        lock.lock()
        guard let endpoint, let token, active else { lock.unlock(); return false }
        lock.unlock()
        var best: (Double, Double)?
        for _ in 0..<4 {
            do {
                var req = URLRequest(url: endpoint.appendingPathComponent("api/time"))
                req.setValue(token, forHTTPHeaderField: "X-Session-Token")
                let t0 = ProcessInfo.processInfo.systemUptime
                let (data, resp) = try await http.data(for: req)
                let t3 = ProcessInfo.processInfo.systemUptime
                guard (resp as? HTTPURLResponse)?.statusCode == 200,
                      let sample = try JSONSerialization.jsonObject(with: data) as? [String: Double],
                      let t1 = sample["t_receive"], let t2 = sample["t_send"] else { continue }
                let rtt = max(0, t3-t0-(t2-t1))
                let value = ((t1-t0)+(t2-t3))/2
                if best == nil || rtt < best!.1 { best = (value,rtt) }
            } catch { continue }
        }
        if let (newOffset, rtt) = best, rtt < 0.08 {
            lock.lock()
            offset = newOffset
            uncertainty = rtt/2
            lastSync = ProcessInfo.processInfo.systemUptime
            lock.unlock()
            await MainActor.run { self.clockUncertaintyMs = rtt*500 }
            return true
        }
        show("Синхронизация часов нестабильна (Wi-Fi RTT ≥ 80 ms)", error: true)
        return false
    }

    func disconnect() {
        syncTask?.cancel()
        syncTask = nil
        lock.lock()
        active = false
        token = nil
        endpoint = nil
        pending = 0
        activeCameraMode = ""
        activeOrientation = ""
        lock.unlock()
        DispatchQueue.main.async {
            self.isStreaming = false
            self.message = "Остановлено. Для нового подключения используй PIN с Mac."
            self.hasError = false
            self.clockUncertaintyMs = nil
        }
    }

    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        let now = ProcessInfo.processInfo.systemUptime
        lock.lock()
        guard active, endpoint != nil, token != nil, now - lastSync < 12,
              uncertainty < 0.045, pending < 1, now-lastFrameSent >= 0.095 else { lock.unlock(); return }
        lastFrameSent = now
        frameId += 1
        pending += 1
        let seq = frameId, adjusted = offset, margin = uncertainty, mode = activeCameraMode
        let server = endpoint!, auth = token!
        lock.unlock()

        guard let buffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { finishFrame(false); return }
        let original = CIImage(cvPixelBuffer: buffer)
        let extent = original.extent
        let aspect = CGFloat(4.0/3.0)
        let cropW = min(extent.width, extent.height*aspect)
        let cropH = cropW/aspect
        let cropX = extent.midX-cropW/2, cropY = extent.midY-cropH/2
        let clipped = original.cropped(to: CGRect(x:cropX, y:cropY, width:cropW, height:cropH))
        let moved = clipped.transformed(by:CGAffineTransform(translationX:-cropX, y:-cropY))
        let scaled = moved.transformed(by:CGAffineTransform(scaleX:640/cropW,y:480/cropH))
        guard let cg = imageContext.createCGImage(scaled, from:CGRect(x:0,y:0,width:640,height:480)),
              let jpeg = UIImage(cgImage:cg).jpegData(compressionQuality:0.58) else { finishFrame(false); return }
        var request = URLRequest(url: server.appendingPathComponent("api/frame"))
        request.httpMethod = "POST"
        request.setValue("image/jpeg", forHTTPHeaderField:"Content-Type")
        request.setValue(auth, forHTTPHeaderField:"X-Session-Token")
        request.setValue(deviceId, forHTTPHeaderField:"X-Device-Id")
        request.setValue(mode, forHTTPHeaderField:"X-Camera-Mode")
        request.setValue(String(seq), forHTTPHeaderField:"X-Frame-Id")
        request.setValue(String(format:"%.7f",now), forHTTPHeaderField:"X-Capture-Mono")
        request.setValue(String(format:"%.9f",adjusted), forHTTPHeaderField:"X-Clock-Offset")
        request.setValue(String(format:"%.9f",margin), forHTTPHeaderField:"X-Clock-Uncertainty")
        http.uploadTask(with: request, from: jpeg) { [weak self] _, response, error in
            let ok = error == nil && (response as? HTTPURLResponse)?.statusCode == 200
            self?.finishFrame(ok)
        }.resume()
    }

    private func finishFrame(_ ok: Bool) {
        lock.lock()
        pending = max(0,pending-1)
        if ok { failures = 0 } else { failures += 1 }
        let failuresNow = failures
        lock.unlock()
        if ok { DispatchQueue.main.async { self.sentFrames += 1 } }
        else if failuresNow == 5 { show("Нет подтверждения от Mac. Проверь соединение и адрес.", error:true) }
    }

    private func show(_ value: String, error: Bool) {
        DispatchQueue.main.async { self.message = value; self.hasError = error }
    }
}
