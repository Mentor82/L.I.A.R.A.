#pragma once
#include <cstdint>

namespace linep {

// ── Message types ─────────────────────────────────────────────────────────────
enum MsgType : uint8_t {
    // Presence
    HEARTBEAT        = 0x01,
    REGISTER         = 0x02,
    REGISTER_ACK     = 0x03,
    BYE              = 0x04,

    // Inference
    TASK             = 0x10,
    TASK_ACK         = 0x11,
    RESULT           = 0x12,
    MSG_ERROR        = 0x13,

    // Status
    STATUS_REQUEST   = 0x20,
    STATUS_RESPONSE  = 0x21,

    // Embedding
    EMBED_REQUEST       = 0x30,
    EMBED_RESPONSE      = 0x31,
    SIMILARITY_REQUEST  = 0x32,
    SIMILARITY_RESPONSE = 0x33,

    // Consensus
    CONSENSUS_REQUEST   = 0x40,
    CONSENSUS_RESPONSE  = 0x41,

    // Diagnostics
    PING = 0xF0,
    PONG = 0xF1,
};

// ── Task types ────────────────────────────────────────────────────────────────
enum TaskType : uint8_t {
    TASK_INSTRUCT       = 0x01,
    TASK_CODE           = 0x02,
    TASK_SUMMARIZE      = 0x03,
    TASK_CLASSIFY       = 0x04,
    TASK_VALIDATE       = 0x05,
    TASK_EDGE_TEXT_EVAL = 0x06,
};

// ── Slot types ────────────────────────────────────────────────────────────────
enum SlotType : uint8_t {
    SLOT_TYPE_INSTRUCT   = 0x01,
    SLOT_TYPE_CODER      = 0x02,
    SLOT_TYPE_EMBEDDING  = 0x03,
    SLOT_TYPE_CLASSIFIER = 0x04,
    SLOT_TYPE_SUMMARIZER = 0x05,
    SLOT_TYPE_VALIDATOR  = 0x06,
};

// ── Result status ─────────────────────────────────────────────────────────────
enum ResultStatus : uint8_t {
    RESULT_OK            = 0x00,
    RESULT_REJECTED      = 0x01,
    RESULT_TIMEOUT       = 0x02,
    RESULT_MODEL_ERROR   = 0x03,
    RESULT_INVALID_INPUT = 0x04,
    RESULT_DEGRADED      = 0x05,
};

// ── Consensus level ───────────────────────────────────────────────────────────
enum ConsensusLevel : uint8_t {
    CONSENSUS_FAILED  = 0,   // < 2/3 agree
    CONSENSUS_PARTIAL = 1,   // 2/3 >= threshold
    CONSENSUS_STRONG  = 2,   // 3/3 >= threshold
};

// ── Error codes ───────────────────────────────────────────────────────────────
enum ErrorCode : uint16_t {
    ERR_PROTOCOL_ERROR       = 1000,
    ERR_CRC_ERROR            = 1001,
    ERR_UNSUPPORTED_VERSION  = 1002,
    ERR_UNKNOWN_MSG_TYPE     = 1003,
    ERR_INVALID_PAYLOAD      = 1004,

    ERR_MODEL_NOT_READY      = 2000,
    ERR_MODEL_LOAD_FAILED    = 2001,
    ERR_INFERENCE_FAILED     = 2002,
    ERR_TOKENIZER_FAILED     = 2003,
    ERR_DEVICE_UNAVAILABLE   = 2004,

    ERR_TIMEOUT              = 3000,
    ERR_NO_SLOT_AVAILABLE    = 3001,
    ERR_CONSENSUS_FAILED     = 3002,
};

} // namespace linep
