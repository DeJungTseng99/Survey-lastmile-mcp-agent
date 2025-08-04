"use client";

import type { ReactNode } from "react";
import {
  AssistantRuntimeProvider,
  useLocalRuntime,
  type ChatModelAdapter,
} from "@assistant-ui/react";

const MyModelAdapter: ChatModelAdapter = {
  async run({ messages, abortSignal }) {
    try {
      // Convert assistant-ui messages to our expected format
      const formattedMessages = messages.map(msg => ({
        role: msg.role,
        content: msg.content.map(part => 
          part.type === "text" ? part.text : part.type
        ).join("")
      }));

      console.log("Sending messages:", formattedMessages);

      const result = await fetch("http://localhost:8000/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          messages: formattedMessages,
        }),
        signal: abortSignal,
      });

      if (!result.ok) {
        const errorText = await result.text();
        console.error("API Error:", result.status, errorText);
        throw new Error(`API Error: ${result.status} - ${errorText}`);
      }

      const data = await result.json();
      console.log("Received data:", data);
      
      // Ensure the response is in the correct format
      return {
        content: data.content || [{ type: "text", text: data.result || "No response" }]
      };
      
    } catch (error) {
      console.error("MyModelAdapter error:", error);
      return {
        content: [{ 
          type: "text", 
          text: `❌ 連接錯誤: ${error instanceof Error ? error.message : "未知錯誤"}` 
        }]
      };
    }
  },
};

export function MyRuntimeProvider({
  children,
}: Readonly<{
  children: ReactNode;
}>) {
  const runtime = useLocalRuntime(MyModelAdapter);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      {children}
    </AssistantRuntimeProvider>
  );
}