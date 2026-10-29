import AppKit
import Contacts
import Foundation

// A named app bundle is needed for a normal macOS Contacts consent prompt.
// It only reads names and addresses; it never edits the user's contact store.
let application = NSApplication.shared
application.setActivationPolicy(.accessory)
let output = URL(fileURLWithPath: CommandLine.arguments[1])

func finish(_ result: [String: Any]) {
    do {
        let data = try JSONSerialization.data(withJSONObject: result)
        try data.write(to: output, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: output.path)
    } catch { /* Python reports a missing result without leaking contact data. */ }
    DispatchQueue.main.async { application.terminate(nil) }
}

DispatchQueue.global(qos: .userInitiated).async {
    let store = CNContactStore()
    let signal = DispatchSemaphore(value: 0)
    var granted = false
    store.requestAccess(for: .contacts) { allowed, _ in
        granted = allowed
        signal.signal()
    }
    signal.wait()
    guard granted else {
        finish(["stage": "permission_needed"])
        return
    }
    let keys = [CNContactGivenNameKey, CNContactMiddleNameKey, CNContactFamilyNameKey,
                CNContactNicknameKey, CNContactOrganizationNameKey,
                CNContactPhoneNumbersKey, CNContactEmailAddressesKey] as [CNKeyDescriptor]
    let request = CNContactFetchRequest(keysToFetch: keys)
    var records: [[String: Any]] = []
    do {
        try store.enumerateContacts(with: request) { contact, _ in
            let full = [contact.givenName, contact.middleName, contact.familyName]
                .filter { !$0.isEmpty }.joined(separator: " ")
            let name = full.isEmpty ? (contact.nickname.isEmpty ? contact.organizationName : contact.nickname) : full
            let addresses = contact.phoneNumbers.map { $0.value.stringValue }
                + contact.emailAddresses.map { String($0.value) }
            records.append(["name": name, "addresses": addresses])
        }
        finish(["stage": "connected", "records": records,
                "limited": CNContactStore.authorizationStatus(for: .contacts).rawValue == 4])
    } catch {
        finish(["stage": "error"])
    }
}
application.run()
