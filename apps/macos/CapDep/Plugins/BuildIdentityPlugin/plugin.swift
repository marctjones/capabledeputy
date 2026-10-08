import Foundation
import PackagePlugin

@main
struct BuildIdentityPlugin: BuildToolPlugin {
    func createBuildCommands(context: PluginContext, target: Target) async throws -> [Command] {
        let generator = try context.tool(named: "BuildIdentityGenerator")
        guard let sourceTarget = target as? SourceModuleTarget else { return [] }
        let packageRoot = context.package.directoryURL
        let versionFile = packageRoot.appending(path: "../../../src/capabledeputy/version.py")
        var inputs = sourceTarget.sourceFiles.map(\.url)
        if FileManager.default.fileExists(atPath: versionFile.path) {
            inputs.append(versionFile)
        }
        let output = context.pluginWorkDirectoryURL.appending(path: "BuildIdentity.generated.swift")
        let arguments = [packageRoot.path, output.path] + inputs.map(\.path)
        return [
            .buildCommand(
                displayName: "Generate CapDep app build identity",
                executable: generator.url,
                arguments: arguments,
                inputFiles: inputs,
                outputFiles: [output],
            ),
        ]
    }
}
