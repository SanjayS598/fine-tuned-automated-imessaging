import AppKit
import WebKit
import Darwin

final class InboxApp: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    private var window: NSWindow!
    private var browser: WKWebView!
    private var backend: Process?
    private var log: FileHandle?
    private var poll: Timer?
    private var attempts = 0
    private var quitting = false
    private let port = 8766
    private let home = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/Personal Inbox", isDirectory: true)

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.appearance = NSAppearance(named: .darkAqua)
        configureMenu()
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 1000)
        let size = NSSize(width: min(1400, screen.width - 60), height: min(980, screen.height - 60))
        window = NSWindow(contentRect: NSRect(origin: .zero, size: size),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Inbox"
        window.minSize = NSSize(width: 900, height: 660)
        window.setFrameAutosaveName("InboxWindow")
        window.backgroundColor = NSColor(calibratedWhite: 0.05, alpha: 1)
        window.delegate = self
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        browser = WKWebView(frame: NSRect(origin: .zero, size: size), configuration: configuration)
        browser.navigationDelegate = self
        browser.uiDelegate = self
        browser.underPageBackgroundColor = NSColor(calibratedWhite: 0.04, alpha: 1)
        window.contentView = browser
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        browser.loadHTMLString("<html><body style='background:#0b0b0d;color:#ddd;font:16px -apple-system;text-align:center;padding-top:25vh'><h1 style='font-weight:400'>inbox.</h1><p style='color:#888'>Opening your private workspace…</p></body></html>", baseURL: nil)
        launchBackend()
    }

    private func configureMenu() {
        let menu = NSMenu()
        let item = NSMenuItem()
        menu.addItem(item)
        let appMenu = NSMenu()
        item.submenu = appMenu
        appMenu.addItem(withTitle: "About Inbox", action: #selector(about), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Messages Access…", action: #selector(permissions), keyEquivalent: "")
        appMenu.addItem(withTitle: "Open Private Data Folder", action: #selector(openData), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit Inbox", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let editItem = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        let editMenu = NSMenu(title: "Edit")
        for (title, selector, key) in [("Undo", "undo:", "z"), ("Cut", "cut:", "x"), ("Copy", "copy:", "c"), ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")] {
            editMenu.addItem(withTitle: title, action: Selector(selector), keyEquivalent: key)
        }
        editItem.submenu = editMenu
        menu.addItem(editItem)
        let viewItem = NSMenuItem(title: "View", action: nil, keyEquivalent: "")
        let viewMenu = NSMenu(title: "View")
        viewMenu.addItem(withTitle: "Back to Inbox", action: #selector(openInbox), keyEquivalent: "1")
        viewMenu.addItem(withTitle: "Refresh Inbox", action: #selector(refresh), keyEquivalent: "r")
        viewItem.submenu = viewMenu
        menu.addItem(viewItem)
        NSApp.mainMenu = menu
        for entry in appMenu.items where entry.action != #selector(NSApplication.terminate(_:)) { entry.target = self }
        viewMenu.items.forEach { $0.target = self }
    }

    private func launchBackend() {
        guard let resources = Bundle.main.resourceURL else { fail("App resources are missing."); return }
        let pythonHome = resources.appendingPathComponent("Python")
        let executable = pythonHome.appendingPathComponent("bin/python3.12")
        // Keep the signed launcher stable across ordinary engine/UI updates.
        // Engine files are installed in the user's private Application Support.
        let installedEngine = home.appendingPathComponent("engine/src")
        let source = FileManager.default.fileExists(atPath: installedEngine.appendingPathComponent("imessage_bot/cli.py").path)
            ? installedEngine : resources.appendingPathComponent("app/src")
        let process = Process()
        process.executableURL = executable
        process.arguments = ["-u", "-m", "imessage_bot.cli", "ui", "--port", String(port), "--no-open"]
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONHOME"] = pythonHome.path
        environment["PYTHONPATH"] = source.path
        environment["PYTHONNOUSERSITE"] = "1"
        environment["IMESSAGE_BOT_HOME"] = home.path
        environment["HF_HUB_OFFLINE"] = "1"
        environment["TRANSFORMERS_OFFLINE"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        process.environment = environment
        process.currentDirectoryURL = home
        let logURL = home.appendingPathComponent("app.log")
        if !FileManager.default.fileExists(atPath: logURL.path) { FileManager.default.createFile(atPath: logURL.path, contents: nil) }
        do {
            log = try FileHandle(forWritingTo: logURL)
            log?.seekToEndOfFile()
            process.standardOutput = log
            process.standardError = log
            process.terminationHandler = { [weak self] stopped in
                DispatchQueue.main.async {
                    guard let self = self, !self.quitting else { return }
                    self.fail("The local server stopped (code \(stopped.terminationStatus)). Details are in the private app.log file.")
                }
            }
            try process.run()
            backend = process
            poll = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in self?.checkReady() }
        } catch { fail("Could not start the bundled runtime: \(error.localizedDescription)") }
    }

    private func checkReady() {
        attempts += 1
        if attempts > 160 { poll?.invalidate(); fail("The inbox could not start. Open the private data folder to inspect app.log."); return }
        let sessionFile = home.appendingPathComponent("ui-url")
        guard let text = try? String(contentsOf: sessionFile, encoding: .utf8),
              let url = URL(string: text), url.host == "127.0.0.1", url.port == port else { return }
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/")!)
        request.timeoutInterval = 1
        URLSession.shared.dataTask(with: request) { [weak self] _, response, _ in
            guard response is HTTPURLResponse else { return }
            DispatchQueue.main.async {
                guard let self = self, self.poll?.isValid == true else { return }
                self.poll?.invalidate()
                self.browser.load(URLRequest(url: url))
            }
        }.resume()
    }

    private func fail(_ message: String) {
        poll?.invalidate()
        let alert = NSAlert()
        alert.messageText = "Inbox needs attention"
        alert.informativeText = message
        alert.addButton(withTitle: "Open Logs")
        alert.addButton(withTitle: "Close")
        if alert.runModal() == .alertFirstButtonReturn { openData() }
    }

    @objc private func about() { NSApp.orderFrontStandardAboutPanel(nil) }
    @objc private func refresh() { browser.reload() }
    @objc private func openInbox() {
        if let text = try? String(contentsOf: home.appendingPathComponent("ui-url"), encoding: .utf8), let url = URL(string: text) {
            browser.load(URLRequest(url: url))
        }
    }
    @objc private func openData() { NSWorkspace.shared.open(home) }
    @objc private func permissions() {
        NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles")!)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        quitting = true
        poll?.invalidate()
        if let process = backend, process.isRunning {
            // SIGINT runs Python's cleanup: pause replies and stop its worker.
            kill(process.processIdentifier, SIGINT)
            let until = Date().addingTimeInterval(5)
            while process.isRunning && Date() < until { Thread.sleep(forTimeInterval: 0.05) }
            if process.isRunning { process.terminate() }
        }
        try? log?.close()
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if url.scheme == "about" { decisionHandler(.allow); return }
        guard url.host == "127.0.0.1", url.port == port else {
            if navigationAction.navigationType == .linkActivated { NSWorkspace.shared.open(url) }
            decisionHandler(.cancel)
            return
        }
        if navigationAction.shouldPerformDownload { decisionHandler(.download) }
        else { decisionHandler(.allow) }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard webView.url?.host == "127.0.0.1" else { return }
        // Content-free launch diagnostics, useful without exposing message text.
        let result: [String: Any] = ["window_visible": window.isVisible, "page_loaded": true,
                                     "app_version": "1.0", "updated": Date().timeIntervalSince1970]
        if let data = try? JSONSerialization.data(withJSONObject: result) {
            try? data.write(to: home.appendingPathComponent("native-health.json"), options: .atomic)
        }
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if navigationAction.targetFrame == nil { webView.load(navigationAction.request) }
        return nil
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        if !navigationResponse.canShowMIMEType { decisionHandler(.download) }
        else { decisionHandler(.allow) }
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                  completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = suggestedFilename
        panel.beginSheetModal(for: window) { result in completionHandler(result == .OK ? panel.url : nil) }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = "Inbox"
        alert.informativeText = message
        alert.addButton(withTitle: "Continue")
        alert.addButton(withTitle: "Cancel")
        completionHandler(alert.runModal() == .alertFirstButtonReturn)
    }
}

let application = NSApplication.shared
let delegate = InboxApp()
application.delegate = delegate
application.setActivationPolicy(.regular)
application.run()
