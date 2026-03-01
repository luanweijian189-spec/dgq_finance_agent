import axios from "axios";
import { WechatyBuilder, log } from "wechaty";

const webhookUrl = process.env.BACKEND_WEBHOOK_URL || "http://127.0.0.1:8000/api/connectors/wechat/webhook";
const roomWhitelist = (process.env.WECHAT_ROOM_WHITELIST || "一起赚钱！")
  .split(",")
  .map((item) => item.trim())
  .filter(Boolean);
const roomKeywords = (process.env.WECHAT_ROOM_KEYWORDS || "一起赚钱")
  .split(",")
  .map((item) => item.trim())
  .filter(Boolean);
const roomIdWhitelist = (process.env.WECHAT_ROOM_ID_WHITELIST || "")
  .split(",")
  .map((item) => item.trim())
  .filter(Boolean);
const forwardAllText = (process.env.WECHAT_FORWARD_ALL_TEXT || "1") === "1";

function normalizeRoomName(name) {
  return (name || "")
    .trim()
    .replace(/[!！?？。,.，、\s]/g, "")
    .toLowerCase();
}

function isRoomAllowed(roomTopic, roomId, topicUnavailable = false) {
  if (roomId && roomIdWhitelist.includes(roomId)) {
    return true;
  }

  if (roomWhitelist.length === 0 && roomKeywords.length === 0) {
    return true;
  }

  if (topicUnavailable) {
    return roomIdWhitelist.length === 0;
  }

  const topic = normalizeRoomName(roomTopic);
  const normalizedWhitelist = roomWhitelist.map(normalizeRoomName);
  const normalizedKeywords = roomKeywords.map(normalizeRoomName);

  if (normalizedWhitelist.includes(topic)) {
    return true;
  }

  for (const item of normalizedWhitelist) {
    if (item && (topic.includes(item) || item.includes(topic))) {
      return true;
    }
  }

  for (const keyword of normalizedKeywords) {
    if (keyword && topic.includes(keyword)) {
      return true;
    }
  }

  return false;
}

const STOCK_REGEX = /(?:^|\D)((?:60|00|30|68)\d{4})(?:\D|$)/;
const INTENT_KEYWORDS = ["看好", "关注", "推荐", "逻辑", "买入", "加仓", "估值", "催化"];

function looksLikeRecommendation(text) {
  if (!text) return false;
  const hasCode = STOCK_REGEX.test(text);
  const hasIntent = INTENT_KEYWORDS.some((k) => text.includes(k));
  return hasCode && hasIntent;
}

async function forwardMessage(payload) {
  try {
    await axios.post(webhookUrl, payload, { timeout: 5000 });
    log.info("relay", `forwarded message from ${payload.recommender_name}`);
  } catch (error) {
    log.error("relay", `forward failed: ${error.message}`);
  }
}

async function safeTalkerName(talker) {
  try {
    return talker ? talker.name() : "unknown";
  } catch (error) {
    log.warn("relay", `talker.name() failed: ${error.message}`);
    return "unknown";
  }
}

async function safeRoomTopic(room) {
  if (!room) {
    return { topic: "", unavailable: false };
  }
  try {
    const topic = await room.topic();
    return { topic, unavailable: false };
  } catch (error) {
    log.warn("relay", `room.topic() failed: ${error.message}`);
    return { topic: "", unavailable: true };
  }
}

const bot = WechatyBuilder.build({ name: "dgq-finance-relay" });

bot.on("scan", (qrcode, status) => {
  log.info("scan", `status=${status} qrcode=${qrcode}`);
  const qrImageUrl = `https://api.qrserver.com/v1/create-qr-code/?size=420x420&data=${encodeURIComponent(qrcode)}`;
  log.info("scan", `scan this image url: ${qrImageUrl}`);
});

bot.on("login", (user) => {
  log.info("login", `${user.name()} logged in`);
  log.info(
    "relay",
    `roomWhitelist=${JSON.stringify(roomWhitelist)} roomKeywords=${JSON.stringify(roomKeywords)} roomIds=${JSON.stringify(roomIdWhitelist)} forwardAllText=${forwardAllText} webhook=${webhookUrl}`,
  );
});

bot.on("message", async (message) => {
  try {
    if (message.self()) return;

    const talker = message.talker();
    const talkerName = await safeTalkerName(talker);
    const room = message.room();
    const roomId = room?.id || "";
    const { topic: roomTopic, unavailable: topicUnavailable } = await safeRoomTopic(room);

    if (room && !isRoomAllowed(roomTopic, roomId, topicUnavailable)) {
      log.info("relay", `skip room: ${roomTopic || "[topic-unavailable]"} roomId=${roomId}`);
      return;
    }

    if (message.type() !== bot.Message.Type.Text) {
      log.info("relay", `skip non-text message in room=${roomTopic || "[private]"} from=${talkerName}`);
      return;
    }

    const text = message.text().trim();
    if (!forwardAllText && !looksLikeRecommendation(text)) {
      log.info(
        "relay",
        `skip non-recommendation room=${roomTopic || "[private]"} from=${talkerName} text=${text.slice(0, 80)}`,
      );
      return;
    }

    log.info("relay", `hit room: ${roomTopic || "[private]"} roomId=${roomId} from=${talkerName}`);

    await forwardMessage({
      message: text,
      recommender_name: talkerName,
      wechat_id: talker?.id || "",
      room_topic: roomTopic,
      source: "wechaty",
    });
  } catch (error) {
    log.error("relay", `message handler failed: ${error.message}`);
  }
});

process.on("uncaughtException", (error) => {
  log.error("relay", `uncaughtException: ${error.message}`);
});

process.on("unhandledRejection", (reason) => {
  log.error("relay", `unhandledRejection: ${reason}`);
});

bot.start().catch((err) => {
  log.error("bot", err.message);
  process.exit(1);
});
