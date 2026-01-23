
import type { TextContent } from "@mariozechner/pi-ai";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { Type } from "@sinclair/typebox";
import { constants } from "fs";
import { access, readFile } from "fs/promises";
import { resolve } from "path";

const readChunkSchema = Type.Object({
	path: Type.String({ description: "Path to the chunk file to read (absolute path)" }),
});

export default function (pi: ExtensionAPI) {
	pi.registerTool({
		name: "read_chunk",
		label: "read_chunk",
		description: "Read the ENTIRE content of a specific chunk file without truncation or size limits. Use this ONLY for reading assigned RLM chunks.",
		parameters: readChunkSchema,

		async execute(_toolCallId, params, _onUpdate, ctx) {
			const { path } = params;
			const absolutePath = resolve(ctx.cwd, path);

			try {
				await access(absolutePath, constants.R_OK);
				const content = await readFile(absolutePath, "utf-8");
				
                // No truncation logic here - return full content
				return {
					content: [{ type: "text", text: content }] as TextContent[],
					details: { bytes: Buffer.byteLength(content, "utf-8") },
				};
			} catch (error: any) {
				return {
					content: [{ type: "text", text: `Error reading chunk file: ${error.message}` }] as TextContent[],
					details: { error: true },
				};
			}
		},
	});
}
