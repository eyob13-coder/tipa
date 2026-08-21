"""Bilingual string catalog (English / Amharic) for the Telegram bot.

Amharic is the biggest adoption lever for Ethiopian creators — the language
picker at /start stores a preference (creators.language in the DB,
context.user_data for anonymous tippers) and every localized surface renders
through ``t()``. Missing keys fall back to English so partial translations
never crash a flow.
"""

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "lang_prompt": "\n\n🌐 **Choose your language:**",
        "start_new": (
            "🎁 **Welcome {user_name} to Tipa (@{bot_name})!**\n"
            "Telegram Tipping for Ethiopian Creators via Mobile Money & Banks.\n\n"
            "Tipa enables followers to tip channel creators directly in Ethiopian Birr (ETB). "
            "Funds flow directly to your Telebirr phone number or bank account — 100% direct and transparent!\n\n"
            "🚀 **How to Get Started (Takes 1 Minute):**\n"
            "1️⃣ Run `/register` to link your payment account.\n"
            "2️⃣ Get your custom tipping deep link (`t.me/{bot_name}?start=tip_<your_id>`).\n"
            "3️⃣ Run `/post` or type `@{bot_name}` to attach a tipping button to your channel posts!\n\n"
            "👇 **Tap `/register` below to get started!**"
        ),
        "start_back": (
            "👋 Welcome back, **{display_name}**!\n"
            "Active Payment Method: **{method}** (`{account_number}`)\n\n"
            "🔗 **Your Personal Channel Tip Link:**\n`{deep_link}`\n\n"
            "📌 **Quick Actions:**\n"
            "• `/post` — Generate channel post & 1-tap tip button\n"
            "• `/mytips` — View your total earnings & supporter notes\n"
            "• `/verifyaccount` — Prove your payout account is yours\n"
            "• `/pro` — Upgrade to Tipa Pro (CSV export & more)\n"
            "• `/export` — Download your tips as a CSV file (Pro)\n"
            "• `/register` — Update your payment details\n"
            "• `/help` — Detailed command guide"
        ),
        "tip_intro": (
            "🎁 **Tip {creator_name}**{post_text}\n"
            "Payment Method: **{method}**\n\n"
            "Choose an amount below to tip directly in Birr (ETB):"
        ),
        "pay_instructions": (
            "{emoji} **{method_name} Tip Payment**\n\n"
            "👤 Recipient: **{recipient}** ({creator_name})\n"
            "{emoji} {account_label}: `{account_number}` *(Tap number to copy)*\n"
            "💰 Amount to Send: **{amount} ETB**{note_display}\n"
            "🔖 Reference Code: `{tx_ref}`\n\n"
            "**How to Pay:**\n"
            "• **Option 1 (App):** Tap **Open {method_name} App** below or open the {method_name} app → Send **{amount} ETB** to `{account_number}`.\n"
            "{ussd_line}\n\n"
            "After sending, tap **I Have Sent the Payment** below to enter your SMS receipt code:"
        ),
        "pay_ussd_line": (
            "• **Option 2 (USSD - No app needed):** Dial `{ussd}` on your phone → Send Money → Enter `{account_number}`."
        ),
        "ocr_processed": (
            "📸 **Receipt Screenshot Processed!**\n"
            "Extracted Reference Code: `{ref}`\n\n"
            "Submitting payment claim for creator verification..."
        ),
        "ref_prompt": (
            "📸 **Receipt Screenshot Received!**\n\n"
            "We received your payment receipt screenshot. "
            "Please type or copy-paste your **Reference / SMS Code** (e.g. `TLB12345678` or `FT12345678`) as text below to complete your claim:"
        ),
        "invalid_ref": (
            "⚠️ **Invalid Reference / SMS Code Format**\n\n"
            "Please enter a valid transaction reference code (e.g. Telebirr `TLB12345678` or CBE `FT12345678`) with at least 6 characters, no spaces or special symbols:"
        ),
        "tip_verified": (
            "✅ **Tip Payment Verified!**\n\n"
            "Ref/SMS Code: `{ref}`\n"
            "Amount: **{amount} ETB**\n\n"
            "Your tip to **{creator_name}** has been confirmed. Thank you for your support! 🙏"
        ),
        "claim_submitted": (
            "✅ **Payment Claim Submitted!**\n\n"
            "Ref/SMS Code: `{ref}`\n"
            "Amount: **{amount} ETB**\n\n"
            "We have notified **{creator_name}**. Once they verify receipt, your tip will be confirmed! 🙏"
        ),
        "mytips_dashboard": (
            "📊 **Creator Dashboard — {display_name}**\n"
            "Payment Method: **{method}** (`{account_number}`)\n\n"
            "💰 **Total Tips Earned:** `{total} ETB`\n"
            "🎉 **Total Tips Received:** `{count}`\n"
            "{pro_line}{av_line}"
            "🔗 **Your Tip Link:**\n`{deep_link}`\n\n"
        ),
        "pro_line_active": "⭐ **Pro:** active until *{date}*\n\n",
        "pro_line_inactive": "⭐ **Pro:** not active — run `/pro` to upgrade!\n\n",
        "av_line_unverified": "⚠️ *Account not verified* — run `/verifyaccount` so tippers can trust your payouts.\n\n",
        "recent_tips_header": "📜 **Recent Tips:**\n",
        "no_tips_yet": "💡 *No successful tips yet. Share your tip link in your Telegram channel to start receiving tips!*",
        "help_text": (
            "📖 **Tipa Bot Command Guide & Help (@{bot_name})**\n\n"
            "**Commands Overview (Tap any command to run):**\n\n"
            "🚀 /start — Welcome screen & deep link handler. Tapping a creator's tip link starts the tipping flow.\n\n"
            "🏦 /register — Register or update your receiving payment method (mobile money or bank). Takes less than 1 minute!\n\n"
            "📢 /addchannel — Link your Telegram channel for auto-tipping.\n\n"
            "📢 /post — Generates a copy-paste post with a 1-tap `[ 🎁 Tip Creator in Birr ]` button for your channel.\n\n"
            "📊 /mytips — Creator dashboard. Shows your total Birr earned, tip count, and recent tips with supporter messages.\n\n"
            "🔐 /verifyaccount — Prove you own your receiving account with a small coded deposit.\n\n"
            "⭐ /pro — Upgrade to Tipa Pro: CSV export, PRO badge, and early access to new features.\n\n"
            "📄 /export — Download your verified tip history as a CSV or PDF file (Pro feature; try `/export pdf`).\n\n"
            "💳 /payout — Switch the bank or mobile wallet your tips are paid into.\n\n"
            "💬 **Supporter Notes** — Tippers can leave an optional encouraging message/note with their tip.\n\n"
            "⚡ **Inline Mode** — Type `@{bot_name}` while composing a post in any Telegram channel to attach a tip button instantly!\n\n"
            "❌ /cancel — Cancel any active registration step or tipping session."
        ),
        "verify_instructions": (
            "🔐 **Verify Account Ownership**\n\n"
            "To protect your tips, prove that `{account_number}` ({method}) belongs to you:\n\n"
            "1️⃣ Send exactly **{amount} ETB** **from your registered {method} account** "
            "to Tipa's account: `{tipa_account}`\n"
            "2️⃣ If your app allows a note/reference, include: `{code}`\n"
            "3️⃣ Tap **I Have Sent the Deposit** below and submit your receipt reference code.\n\n"
            "⚠️ The deposit must come from the account you registered — it's checked against your verification code."
        ),
        "pro_pitch": (
            "⭐ **Tipa Pro** — {price} ETB / {duration} days\n\n"
            "{status_line}"
            "🔓 **What you unlock:**\n"
            "• 📄 CSV export of all your verified tips\n"
            "• ⭐ PRO badge on your tipping page\n"
            "• 🚀 Early access to new Pro features as they ship\n"
            "• ❤️ Directly supports Tipa's development\n\n"
            "{emoji} **How to pay:**\n"
            "Send **{price} ETB** to {method_name} `{tipa_account}` ({account_label}), then submit your receipt reference below.\n\n"
            "🔖 Your payment reference code: `{tx_ref}`\n"
            "(You'll enter the *SMS receipt code* from {method_name} after paying.)"
        ),
        "payout_intro": (
            "💳 **Update Payout Details**\n\n"
            "Current payout method: **{current}**\n"
            "Pick a new bank or mobile wallet below — your tipping link stays the same."
        ),
        "goal_set": (
            "🎯 **Goal set!**\n\n{line}\n\n"
            "📢 Run `/post` to attach the progress bar to a channel post — "
            "it updates automatically every time a tip lands.\n"
            "Change it anytime: `/goal <target> <title>` or remove with `/endgoal`."
        ),
        "goal_usage": (
            "🎯 **Set a fundraising goal**\n\n"
            "Usage: `/goal 10000 New camera`\n\n"
            "Followers will see a live progress bar as tips come in."
        ),
        "goal_cancelled": "🗑️ Goal removed. Set a new one with `/goal <target> <title>`.",
        "goal_none": "ℹ️ You have no active goal. Create one with `/goal <target> <title>`.",
        "topfans_header": "🏆 **Top Fans This Month**\n\n",
        "topfans_empty": (
            "🤔 No verified tips yet this month.\n"
            "Share your tip link — your biggest supporters will show up here!"
        ),
        "digest_top_fan": "**Top fan this week:**",
        "digest_text": (
            "📊 **Your Week on Tipa**\n\n"
            "💰 Earned: **{earned} ETB** from **{count}** tip(s)\n"
            "{top_line}{goal_line}"
            "Keep sharing your tip link — every tip lands straight in your {method} account."
        ),
    },
    "am": {
        "lang_prompt": "\n\n🌐 **ቋንቋዎን ይምረጡ / Choose your language:**",
        "start_new": (
            "🎁 **እንኳን {user_name} ወደ Tipa (@{bot_name}) በደህና መጡ!**\n"
            "ለኢትዮጵያ የይዘት ፈጣሪዎች በሞባይል ገንዘብ እና በባንክ በኩል የቴሌግራም ስጦታ (Tip)።\n\n"
            "Tipa ተከታዮች ለቻናል ፈጣሪዎች በቀጥታ በኢትዮጵያ ብር (ETB) እንዲሰጡ ያስችላል። "
            "ገንዘቡ በቀጥታ ወደ ቴሌብር ስልክ ቁጥርዎ ወይም ወደ ባንክ ሂሳብዎ ይሄዳል — 100% ቀጥታ እና ግልጽ!\n\n"
            "🚀 **እንዴት እንደሚጀምሩ (በ1 ደቂቃ ውስጥ):**\n"
            "1️⃣ የክፍያ ሂሳብዎን ለማስገናት `/register` ይጫኑ።\n"
            "2️⃣ የራስዎን የስጦታ ሊንክ ያግኙ (`t.me/{bot_name}?start=tip_<your_id>`)።\n"
            "3️⃣ ለቻናል ፖስቶችዎ የስጦታ አዝራር ለማያያዝ `/post` ይጫኑ ወይም `@{bot_name}` ይጻፉ!\n\n"
            "👇 **ለመጀመር ከታች `/register` ይንኩ!**"
        ),
        "start_back": (
            "👋 እንኳን ደህና መጡ፣ **{display_name}**!\n"
            "ንቁ የክፍያ መንገድ: **{method}** (`{account_number}`)\n\n"
            "🔗 **የግል የቻናል ስጦታ ሊንክዎ፦**\n`{deep_link}`\n\n"
            "📌 **ፈጣን ተግባራት፦**\n"
            "• `/post` — የቻናል ፖስት እና በአንድ ንክኪ የስጦታ አዝራር ይፍጠሩ\n"
            "• `/mytips` — ጠቅላላ ገቢዎን እና የደጋፊዎች መልእክቶችን ይመልከቱ\n"
            "• `/verifyaccount` — የክፍያ ሂሳብዎ የእርስዎ መሆኑን ያረጋግጡ\n"
            "• `/pro` — ወደ Tipa Pro ያሳዩ (CSV ማውጣት እና ሌሎችም)\n"
            "• `/export` — ስጦታዎችዎን እንደ CSV ፋይል ያውርዱ (Pro)\n"
            "• `/register` — የክፍያ ዝርዝሮችዎን ያዘምኑ\n"
            "• `/help` — ዝርዝር የትእዛዝ መመሪያ"
        ),
        "tip_intro": (
            "🎁 **ለ{creator_name} ስጦታ (Tip)**{post_text}\n"
            "የክፍያ መንገድ: **{method}**\n\n"
            "በቀጥታ በብር (ETB) ለመላክ ከታች መጠን ይምረጡ፦"
        ),
        "pay_instructions": (
            "{emoji} **በ{method_name} መለገፍ (Tip)**\n\n"
            "👤 ተቀባይ: **{recipient}** ({creator_name})\n"
            "{emoji} {account_label}: `{account_number}` *(ለመቅዳት ቁጥሩን ይንኩ)*\n"
            "💰 የሚልኩት መጠን: **{amount} ETB**{note_display}\n"
            "🔖 ማጣቀሻ ኮድ: `{tx_ref}`\n\n"
            "**እንዴት እንደሚከፍሉ፦**\n"
            "• **አማራጭ 1 (መተግበሪያ):** ከታች **{method_name} መተግበሪያ ይክፈቱ** የሚለውን ይንኩ ወይም የ{method_name} መተግበሪያውን ይክፈቱ → **{amount} ETB** ወደ `{account_number}` ይላኩ።\n"
            "{ussd_line}\n\n"
            "ከላኩ በኋላ፣ የSMS ማረጋገጫ ኮድዎን ለማስገባት ከታች **ክፍያዬን ላኩ** የሚለውን ይንኩ፦"
        ),
        "pay_ussd_line": (
            "• **አማራጭ 2 (USSD - መተግበሪያ አያስፈልግም)፦** በስልክዎ `{ussd}` ይደውሉ → Send Money ይምረጡ → `{account_number}` ያስገቡ።"
        ),
        "ocr_processed": (
            "📸 **የክፍያ ማረጋገጫ ተከናውኗል!**\n"
            "የተሰረዘው ማጣቀሻ ኮድ፦ `{ref}`\n\n"
            "የክፍያ ይግባኝዎ ለፈጣሪ ማረጋገጫ በመላክ ላይ ነው..."
        ),
        "ref_prompt": (
            "📸 **የክፍያ ማረጋገጫ (Screenshot) ተቀብለናል!**\n\n"
            "እባክዎ የ**ማጣቀሻ / SMS ኮድ**ዎን (ለምሳሌ `TLB12345678` ወይም `FT12345678`) እንደ ጽሑፍ በታች ይጻፉ ወይም ይለጥፉ፦"
        ),
        "invalid_ref": (
            "⚠️ **ልክ ያልሆነ የማጣቀሻ ኮድ**\n\n"
            "እባክዎ ትክክለኛ የአገልግሎት ማስረጃ ኮድ ያስገቡ (ለምሳሌ Telebirr `TLB12345678` ወይም CBE `FT12345678`) — ቢያንስ 6 ፊደላት፣ ክፍተት ወይም ልዩ ምልክቶች ያለ፦"
        ),
        "tip_verified": (
            "✅ **የስጦታ ክፍያ ተረጋግጧል!**\n\n"
            "ማጣቀሻ/SMS ኮድ፦ `{ref}`\n"
            "መጠን፦ **{amount} ETB**\n\n"
            "ለ**{creator_name}** የላኩት ስጦታ ተረጋግጧል። ስለድጋፍዎ እናመሰግናለን! 🙏"
        ),
        "claim_submitted": (
            "✅ **የክፍያ ይግባኝ ገብቷል!**\n\n"
            "ማጣቀሻ/SMS ኮድ፦ `{ref}`\n"
            "መጠን፦ **{amount} ETB**\n\n"
            "**{creator_name}** ተነግሮታል። ወደደው እንደተቀበሉ ከረጋገጡ ስጦታዎ ይረጋገጣል! 🙏"
        ),
        "mytips_dashboard": (
            "📊 **የፈጣሪ ዳሽቦርድ — {display_name}**\n"
            "የክፍያ መንገድ: **{method}** (`{account_number}`)\n\n"
            "💰 **ጠቅላላ የተገኘ፦** `{total} ETB`\n"
            "🎉 **ጠቅላላ የተቀበሉ ስጦታዎች፦** `{count}`\n"
            "{pro_line}{av_line}"
            "🔗 **የስጦታ ሊንክዎ፦**\n`{deep_link}`\n\n"
        ),
        "pro_line_active": "⭐ **Pro:** እስከ *{date}* ንቁ ነው\n\n",
        "pro_line_inactive": "⭐ **Pro:** አልነቃቀረም — ለማሳደግ `/pro` ይጫኑ!\n\n",
        "av_line_unverified": "⚠️ *ሂሳብዎ አልረጋገጠም* — ክፍያዎችዎ ሊታመኑ እንዲችሉ `/verifyaccount` ይጫኑ።\n\n",
        "recent_tips_header": "📜 **የቅርብ ጊዜ ስጦታዎች፦**\n",
        "no_tips_yet": "💡 *እስካሁን ስጦታ የለም። ለመቀበል የስጦታ ሊንክዎን በቻናልዎ ያጋሩ!*",
        "help_text": (
            "📖 **የTipa Bot ትእዛዞች እና እገዛ (@{bot_name})**\n\n"
            "**የትእዛዞች አጠቃላይ እይታ (ለማስኬድ ትእዛዙን ይንኩ)፦**\n\n"
            "🚀 /start — የእንግዳ ተቀባይ ገጽ እና የሊንክ አስተናጋጅ። የፈጣሪ ስጦታ ሊንክ መንካት የመለገፍ ሂደቱን ይጀምራል።\n\n"
            "🏦 /register — የክፍያ መንገድዎን ይመዝገቡ ወይም ያዘምኑ (ሞባይል ገንዘብ ወይም ባንክ)። ከ1 ደቂቃ ያነሰ!\n\n"
            "📢 /addchannel — ለራስ-ሰር መለገፍ የቴሌግራም ቻናልዎን ያገናኙ።\n\n"
            "📢 /post — ለቻናልዎ በአንድ ንክኪ `[ 🎁 Tip Creator in Birr ]` አዝራር ያለው ፖስት ይፍጠራል።\n\n"
            "📊 /mytips — የፈጣሪ ዳሽቦርድ። ጠቅላላ ብር ገቢዎን፣ የስጦታ ብዛትዎን እና የደጋፊ መልእክቶችን ያሳያል።\n\n"
            "🔐 /verifyaccount — በትንሽ ኮድ ያለው ተቀንጭቦ የክፍያ ሂሳብዎ የእርስዎ መሆኑን ያረጋግጡ።\n\n"
            "⭐ /pro — ወደ Tipa Pro ያሳዩ፦ CSV ማውጣት፣ PRO ምልክት እና አዳዲስ ባህሪያት ቀደምት መዳረሻ።\n\n"
            "📄 /export — የተረጋገጡ ስጦታዎችዎን ታሪክ እንደ CSV ወይም PDF ፋይል ያውርዱ (የPro ባህሪ; `/export pdf` ይሞክሩ)።\n\n"
            "💳 /payout — ስጦታዎችዎ የሚከፈሉበትን ባንክ ወይም ሞባይል ዋሌት ይለውጡ።\n\n"
            "💬 **የደጋፊ መልእክቶች** — ስጦታ ከሚልኩ ጋር አማራጭ የማበረታቻ መልእክት ማካተት ይችላሉ።\n\n"
            "⚡ **Inline ሁኔታ** — በማንኛውም ቻናል ፖስት ላይ ሲጽፉ `@{bot_name}` ይጻፉ እና የስጦታ አዝራር በፍጥነት ያያይዙ!\n\n"
            "❌ /cancel — ማንኛውንም ንቁ ምዝገባ ወይም የመለገፍ ክፍለ-ጊዜ ይሰርዙ።"
        ),
        "verify_instructions": (
            "🔐 **የሂሳብ ባለቤትነት ማረጋገጫ**\n\n"
            "ስጦታዎችዎን ለመጠበቅ፣ `{account_number}` ({method}) የእርስዎ መሆኑን ያረጋግጡ፦\n\n"
            "1️⃣ ከተመዘገበው {method} ሂሳብዎ በትክክል **{amount} ETB** ወደ Tipa ሂሳብ `{tipa_account}` ይላኩ።\n"
            "2️⃣ መተግበሪያዎ ማስታወሻ/ማጣቀሻ ከፈቀደ `{code}` ይጻፉ።\n"
            "3️⃣ ከታች **ተቀንጭቤ ላኩ** የሚለውን ይንኩ እና የደረሰኝ ማጣቀሻ ኮድዎን ያስገቡ።\n\n"
            "⚠️ ተቀንጭቡ ከተመዘገበው ሂሳብዎ መሆን አለበት — ከማረጋገጫ ኮድዎ ጋር ይነጻጸራል።"
        ),
        "pro_pitch": (
            "⭐ **Tipa Pro** — {price} ETB / {duration} ቀናት\n\n"
            "{status_line}"
            "🔓 **የሚያገኙት፦**\n"
            "• 📄 ሁሉንም የተረጋገጡ ስጦታዎችዎን CSV ማውጣት\n"
            "• ⭐ በስጦታ ገጽዎ ላይ PRO ምልክት\n"
            "• 🚀 አዳዲስ የPro ባህሪያት ቀደምት መዳረሻ\n"
            "• ❤️ የTipaን ልማት በቀጥታ ይደግፋል\n\n"
            "{emoji} **እንዴት እንደሚከፍሉ፦**\n"
            "**{price} ETB** ወደ {method_name} `{tipa_account}` ({account_label}) ይላኩ፣ ከዚያ የደረሰኝ ማጣቀሻዎን ከታች ያስገቡ።\n\n"
            "🔖 የክፍያ ማጣቀሻ ኮድዎ፦ `{tx_ref}`\n"
            "(ከከፈሉ በኋላ ከ{method_name} የሚላከውን *SMS ማረጋገጫ ኮድ* ያስገባሉ።)"
        ),
        "payout_intro": (
            "💳 **የክፍያ ዝርዝሮችን ማዘመን**\n\n"
            "የአሁኑ የክፍያ መንገድ: **{current}**\n"
            "ከታች አዲስ ባንክ ወይም ሞባይል ዋሌት ይምረጡ — የስጦታ ሊንክዎ ሳይቀየር ይቀራል።"
        ),
        "goal_set": (
            "🎯 **ግብ ተመዘገበ!**\n\n{line}\n\n"
            "📢 የሂደት አሞሌውን ወደ ቻናል ፖስት ለማያያዝ `/post` ይጫኑ — "
            "ስጦታ በሚደርስበት ጊዜ በራስ-ሰር ይቀየራል።\n"
            "በፈለጉ ጊዜ ይለውጡ፦ `/goal <target> <title>` ወይም `/endgoal` ይሰርዙ።"
        ),
        "goal_usage": (
            "🎯 **የገቢ ማሰባሰቢያ ግብ ይመዝገቡ**\n\n"
            "አጠቃቀም፦ `/goal 10000 New camera`\n\n"
            "ተከታዮችዎ ስጦታ ሲገባ በቀጥታ የሚቀየር የሂደት አሞሌ ያያሉ።"
        ),
        "goal_cancelled": "🗑️ ግቡ ተሰርዟል። አዲስ ለመመዝገብ `/goal <target> <title>` ይጫኑ።",
        "goal_none": "ℹ️ ንቁ ግብ የለዎትም። ለመመዝገብ `/goal <target> <title>` ይጫኑ።",
        "topfans_header": "🏆 **የዚህ ወር ከፍተኛ ደጋፊዎች**\n\n",
        "topfans_empty": (
            "🤔 በዚህ ወር እስካሁን የተረጋገጠ ስጦታ የለም።\n"
            "የስጦታ ሊንክዎን ያጋሩ — ትልልቅ ደጋፊዎችዎ እዚህ ይታያሉ!"
        ),
        "digest_top_fan": "**የዚህ ሳምንት ኮከብ ደጋፊ:**",
        "digest_text": (
            "📊 **በTipa ላይ የሚቀጥለው ሳምንትዎ**\n\n"
            "💰 ገቢ፦ **{earned} ETB** ከ**{count}** ስጦታ(ዎች)\n"
            "{top_line}{goal_line}"
            "የስጦታ ሊንክዎን መጋራትዎን ይቀጥሉ — እያንዳንዱ ስጦታ በቀጥታ ወደ {method} ሂሳብዎ ይገባል።"
        ),
    },
}


def t(lang: str, key: str, **kwargs) -> str:
    """Render a localized string; falls back to English, then the key itself."""
    template = STRINGS.get(lang, {}).get(key) or STRINGS["en"].get(key) or key
    if kwargs:
        return template.format(**kwargs)
    return template
