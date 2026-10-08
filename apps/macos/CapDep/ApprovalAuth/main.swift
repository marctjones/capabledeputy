import Foundation
import LocalAuthentication

// Launched only by the daemon from an operator-pinned installation. No CLI
// success flag, cached proof or caller-provided authentication result exists.
guard let request = try? JSONSerialization.jsonObject(with: FileHandle.standardInput.readDataToEndOfFile()) as? [String: String],
      let binding = request["binding"], binding.count == 64,
      let nonce = request["nonce"], nonce.count == 64,
      let reason = request["reason"], !reason.isEmpty, reason.count <= 180 else { exit(2) }
let context = LAContext()
context.touchIDAuthenticationAllowableReuseDuration = 0
var error: NSError?
guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) else { exit(3) }
context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: reason) { success, _ in
    guard success else { exit(4) }
    let proof: [String: Any] = ["binding": binding, "nonce": nonce, "authenticated": true]
    guard let data = try? JSONSerialization.data(withJSONObject: proof) else { exit(5) }
    FileHandle.standardOutput.write(data)
    exit(0)
}
RunLoop.main.run()
