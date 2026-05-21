import SwiftUI
import UIKit

final class KeyboardViewController: UIInputViewController {
    private var hostingController: UIHostingController<KeyboardView>?
    private let bridge = KeyboardBridge()

    override func viewDidLoad() {
        super.viewDidLoad()

        bridge.advanceToNextInputAction = { [weak self] in
            self?.advanceToNextInputMode()
        }
        bridge.insertTextAction = { [weak self] text in
            self?.textDocumentProxy.insertText(text)
        }
        bridge.deleteBackwardAction = { [weak self] in
            self?.textDocumentProxy.deleteBackward()
        }
        bridge.insertNewlineAction = { [weak self] in
            self?.textDocumentProxy.insertText("\n")
        }

        bridge.hasFullAccess = hasFullAccess

        let root = KeyboardView(bridge: bridge)
        let host = UIHostingController(rootView: root)
        host.view.translatesAutoresizingMaskIntoConstraints = false
        host.view.backgroundColor = .clear
        addChild(host)
        view.addSubview(host.view)
        NSLayoutConstraint.activate([
            host.view.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            host.view.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            host.view.topAnchor.constraint(equalTo: view.topAnchor),
            host.view.bottomAnchor.constraint(equalTo: view.bottomAnchor),
        ])
        host.didMove(toParent: self)
        self.hostingController = host
    }

}
