# Privacy Policy — Peekoro-bot

**Last updated:** April 7, 2026

This policy describes how **Peekoro-bot** handles information when you use it. Peekoro-bot provides [FIRST Robotics Competition](https://www.firstinspires.org/robotics/frc) data in Discord via slash commands, using the [Peekorobo](https://www.peekorobo.com) API.

If someone else runs a copy of this software, **that person (the operator of that instance)** is responsible for their deployment and for answering privacy questions about that instance. This document describes what Peekoro-bot’s code is designed to do.

---

## Who this applies to

- **Discord users** who interact with Peekoro-bot (run slash commands, click buttons).
- **Server administrators** who add Peekoro-bot to a Discord server.

---

## Information Peekoro-bot processes

### Account and technical data (Discord)

To respond to slash commands and buttons, Discord sends Peekoro-bot typical interaction data, including:

- Your **Discord user ID** (used to associate stored settings with your account).
- **Server (guild) and channel context** needed to post replies where the command was used.
- **Command names, options, and button usage** (for example, team numbers or event keys you typed).

Peekoro-bot uses **default Discord intents** and does **not** read ordinary message content in channels.

### Peekorobo API key (optional but required for most commands)

If you run **`/peek_auth`**, you may submit a **Peekorobo API key**. Peekoro-bot verifies the key with the Peekorobo API, then **stores it** so your commands can authenticate to the API.

Stored data (on the machine running Peekoro-bot):

| Item | Purpose |
|------|---------|
| Discord user ID | Identify which saved key belongs to which user |
| API key | Authenticate your requests to the Peekorobo API |
| Timestamp of last update | Basic record-keeping for the stored key |

You can remove the stored key anytime with **`/peek_auth_clear`**.

### Data from the Peekorobo API

When you use commands that fetch FRC data, Peekoro-bot requests information from the Peekorobo API and may display it in Discord (for example, embeds, CSV/JSON exports). That content is largely **public or competition-related data** (teams, events, matches, rankings, metrics), not private messages you wrote elsewhere.

### Third-party services

- **Discord, Inc.** hosts Discord and processes data under [Discord’s policies](https://discord.com/privacy).
- **Peekorobo** hosts the API and processes API requests under its own terms and policies. See [peekorobo.com](https://www.peekorobo.com).

---

## How we use this information

- **Operate slash commands** and send replies in Discord.
- **Authenticate** to the Peekorobo API using your stored key (or, if configured by the operator, a separate key limited to designated bot owners).
- **Improve reliability** (for example, handling errors and timeouts when talking to the API).

Peekoro-bot is **not** intended for profiling, advertising, or selling personal data.

---

## Sharing of information

- **Peekorobo:** Your API key is sent to the Peekorobo API as an `X-API-Key` header when you run data commands, consistent with normal API use.
- **Discord:** Replies and interactions are handled through Discord’s systems; visibility follows Discord’s rules (for example, channel visibility, ephemeral responses where used).
- **Other parties:** Peekoro-bot’s design does **not** send your stored API key to unrelated third parties. The operator could still be compelled by law or modify the software; if you use a **third-party instance**, you trust that operator.

---

## Storage and security

- API keys are stored in a **SQLite database file** on the server running Peekoro-bot (`user_api_keys.sqlite3` in Peekoro-bot’s directory, unless the operator changes the deployment).
- Security depends on the **operator’s hosting practices** (file permissions, backups, who has server access). Use **`/peek_auth_clear`** if you stop trusting an instance, and **rotate your Peekorobo API key** at the source if you believe it was exposed.

---

## Retention

- Stored API keys remain until you **delete them with `/peek_auth_clear`**, the operator **deletes the database**, or the **Bot is removed** from service.
- Peekoro-bot does not need to retain chat history for its core features; operational logs depend on the operator’s configuration.

---

## Your choices

- **Don’t use Peekoro-bot** if you do not want Discord interaction data processed as described.
- **Don’t run `/peek_auth`** if you do not want an API key stored on that instance (most data commands will not work without a key).
- **Use `/peek_auth_clear`** to remove your stored key from that instance.
- **Rotate or revoke** your Peekorobo API key on the Peekorobo side if needed.

---

## Children

Peekoro-bot is not directed at children under 13. Discord requires users to meet Discord’s minimum age for their region.

---

## International users

Discord and Peekorobo may process data in various countries. By using Peekoro-bot, your information may be handled according to those services’ practices.

---

## Changes

The operator may update this policy when Peekoro-bot or practices change. The **“Last updated”** date at the top will be revised for material changes when possible.

---

## Contact

For privacy questions about **this Bot instance**, contact the person or organization that invited Peekoro-bot to your server or that published the instance you use.

For questions about **Peekorobo’s API or website**, use the contact or support options listed on [peekorobo.com](https://www.peekorobo.com).

---

*This document is provided for transparency and is not legal advice.*
