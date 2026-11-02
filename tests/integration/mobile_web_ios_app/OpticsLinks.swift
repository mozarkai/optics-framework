import SafariServices
import SwiftUI
import WebKit

private let pageURL = URL(string: UserDefaults.standard.string(forKey: "fixtureURL") ?? "about:blank")!

struct SafariView: UIViewControllerRepresentable {
    func makeUIViewController(context _: Context) -> SFSafariViewController { SFSafariViewController(url: pageURL) }

    func updateUIViewController(_: SFSafariViewController, context _: Context) {
        // The sheet shows one fixed page; there is no state to push into it.
    }
}

final class FixtureNavigation: NSObject, WKNavigationDelegate {
    func webView(
        _: WKWebView,
        decidePolicyFor navigationAction: WKNavigationAction,
        decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
    ) {
        decisionHandler(navigationAction.request.url?.host == pageURL.host ? .allow : .cancel)
    }
}

struct InspectableWebView: UIViewRepresentable {
    var url = pageURL

    func makeCoordinator() -> FixtureNavigation { FixtureNavigation() }

    func makeUIView(context: Context) -> WKWebView {
        let webView = WKWebView()
        webView.isInspectable = UserDefaults.standard.bool(forKey: "inspectable")
        webView.navigationDelegate = context.coordinator
        webView.load(URLRequest(url: url))
        return webView
    }

    func updateUIView(_: WKWebView, context _: Context) {
        // The page loads once in makeUIView; SwiftUI state never changes it.
    }
}

struct LinksView: View {
    @Environment(\.openURL) private var openURL
    @State private var showSafariView = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 24) {
                Button("Open in Safari") { openURL(pageURL) }
                Button("Open in Safari View") { showSafariView = true }
                NavigationLink("Open in Web View") { InspectableWebView().ignoresSafeArea(edges: .bottom) }
                NavigationLink("Open Two Web Views") {
                    VStack(spacing: 0) {
                        InspectableWebView(url: pageURL.appendingPathComponent("banner.html")).frame(height: 160)
                        InspectableWebView().ignoresSafeArea(edges: .bottom)
                    }
                }
            }
            .buttonStyle(.borderedProminent)
            .navigationTitle("Optics Links")
            .fullScreenCover(isPresented: $showSafariView) { SafariView().ignoresSafeArea() }
        }
    }
}

@main
struct OpticsLinksApp: App {
    var body: some Scene {
        WindowGroup { LinksView() }
    }
}
