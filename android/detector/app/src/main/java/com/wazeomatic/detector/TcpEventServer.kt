/*
Module Name: TcpEventServer.kt
Description: Receives detections from the Raspberry Pi or desktop simulator in the external camera mode

Class Name: TcpEventServer
Description: Accepts newline-delimited HazardEvent JSON on a TCP port and answers each line with ok or an error

Inputs: JSON lines from the Raspberry Pi or desktop over TCP
Outputs: HazardEvents passed to the Reporter
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to write the receiver for the external camera mode
How you validated and revised the AI output: Proofreading, and sending events from the desktop simulator with the TCP sink
The challenges or limitations you faced while using AI: Answering malformed lines with an error instead of closing the connection
Authors: Blake Pennel
Creation Date: 11/5/2026
*/

package com.wazeomatic.detector

import android.util.Log
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.Collections

/**
 * Accepts newline-delimited HazardEvent JSON (schema.py shape) from the Raspberry Pi or the
 * desktop simulator. Each valid line is passed to [onEvent]; the sender gets "ok" or "error ...".
 */
class TcpEventServer(private val port: Int, private val onEvent: (HazardEvent) -> Unit) {
    @Volatile private var server: ServerSocket? = null
    private val clients = Collections.synchronizedSet(HashSet<Socket>())
    private var thread: Thread? = null

    fun start() {
        thread = Thread({ acceptLoop() }, "tcp-accept").apply { isDaemon = true; start() }
    }

    private fun acceptLoop() {
        try {
            val ss = ServerSocket()
            ss.reuseAddress = true
            ss.bind(InetSocketAddress(port))
            server = ss
            Log.i(TAG, "tcp listener on port $port")
            while (!ss.isClosed) {
                val s = ss.accept()
                clients.add(s)
                Thread({ handle(s) }, "tcp-client").apply { isDaemon = true; start() }
            }
        } catch (e: Exception) {
            if (server?.isClosed != true) Log.e(TAG, "tcp listener stopped", e)
        }
    }

    private fun handle(s: Socket) {
        Log.i(TAG, "tcp client ${s.remoteSocketAddress}")
        try {
            val reader = BufferedReader(InputStreamReader(s.getInputStream(), Charsets.UTF_8))
            val out = s.getOutputStream()
            while (true) {
                val line = reader.readLine() ?: break
                if (line.isBlank()) continue
                val reply = try {
                    onEvent(HazardEvent.fromJson(line.trim()))
                    "ok\n"
                } catch (e: Exception) {
                    Log.w(TAG, "bad event line: $line (${e.message})")
                    "error ${e.message}\n"
                }
                try {
                    out.write(reply.toByteArray())
                    out.flush()
                } catch (_: Exception) {
                }
            }
        } catch (_: Exception) {
        } finally {
            clients.remove(s)
            try { s.close() } catch (_: Exception) {}
        }
    }

    fun stop() {
        try { server?.close() } catch (_: Exception) {}
        synchronized(clients) {
            clients.forEach { try { it.close() } catch (_: Exception) {} }
            clients.clear()
        }
    }

    companion object {
        private const val TAG = "Wazeomatic"
    }
}
