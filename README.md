# OAuth migration demo

We've built a working OAuth demo in Flask to help you expedite your migration. Follow the steps below to get started.

## Important disclaimer
All code samples in this repository show examples of how to accomplish certain use cases. We will use our best effort to maintain these examples, but occasionally some items may break. If you notice a broken code sample, please open an issue to let us know something is broken, or alternatively submit a PR with a proposed fix.

## Version

* API revision: 2025-07-15

## Step 1: Get your Klaviyo OAuth app credentials

1. Go to your Klaviyo Developer Console.
2. Create an OAuth app.
3. Add the redirect URL: `http://localhost:5000/auth/callback`.
4. Add the following scopes: accounts:read profiles:read.
5. Copy your Client ID and Client Secret.

## Step 2: Clone the demo project

First, run the following code to set up your virtual environment:
```bash
mkdir klaviyo-oauth-demo && cd klaviyo-oauth-demo
python3 -m venv .venv
source .venv/bin/activate
pip install flask requests python-dotenv
```

Save the `demo_oauth_flow.py` file to your computer, and create a `.env.local` file with the following credentials:
```
CLIENT_ID=your_client_id_here
CLIENT_SECRET=your_client_secret_here
REDIRECT_URI=http://localhost:5000/auth/callback
KLAVIYO_SCOPES="accounts:read profiles:read"
FLASK_SECRET_KEY=super-secret-for-local-dev
```

Note: for production, do not hardcode FLASK_SECRET_KEY.
Instead, generate a strong random GUID/hex string and import via a secure env var or secrets manager.


## Step 3: Run the OAuth flow locally

Start the server:
```bash
python oauth_flow.py
```
Kick off the flow in your browser: `http://localhost:5000/auth/start`
You'll log into Klaviyo, grant consent, and immediately get back your access token and refresh token in JSON.

## Step 4: Refresh tokens automatically

We've built a `/auth/refresh` endpoint to handle token rotation for you:
```bash
curl -X POST http://localhost:5000/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "YOUR_REFRESH_TOKEN"}'
```
This ensures you'll never run into expired token issues.

## Step 5: Test real Klaviyo API calls

Use the `/whoami` endpoint with your access token to hit Klaviyo's API directly:
```bash
curl -X GET http://localhost:5000/whoami \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```
This verifies end-to-end that your migration works.

## What this means for your team
* __Engineering leaders:__ You don't need to commit weeks of dev time. With our starter code, your teams can cut migration down to a few days.
* __Security & compliance:__ OAuth eliminates key sprawl, enables scoped permissions, and simplifies audits.
* __Future-proofing:__ This is the industry standard. Your engineers will integrate faster with other modern systems once OAuth is the norm.

## How to productionize your OAuth migration
The demo code gets you from private key → OAuth in minutes. But before rolling this into production, here are the must-dos for a secure, scalable integration:

### 1. Store tokens in your database
Save both the access token and refresh token securely in your database, encrypted at rest. Record `expires_in` (or calculate `expires_at`) so you can proactively refresh before expiry.

### 2. Handle token expiry gracefully
Every Klaviyo API call must include the access token in the `Authorization: Bearer <token>` header. If you receive a `401 Unauthorized`, your access token has expired. At that point:
1. Use the refresh token to request a new access token
2. Save the new tokens to your DB
3. Retry the original API call transparently
This ensures a seamless experience with no downtime for your users.

### 3. Migrate your existing users
Any users who are using the private key integration will need to re-authenticate using OAuth. Build a simple re-authentication flow into your app to request consent from each user. Once migrated, you can deprecate private key support entirely.

### 4. Metrics auto migration
Good news: any metrics previously tracked via private keys will automatically migrate to branded metrics. This means your data continuity is preserved, and you get all the benefits of OAuth without losing visibility. Learn more about branded metrics here.

## Additional resources
* Developer docs:
  * [Make API calls using OAuth](https://developers.klaviyo.com/en/docs/set_up_oauth)
  * [Create a public OAuth app](https://developers.klaviyo.com/en/docs/create_a_public_oauth_app)
  * [Handle your app's OAuth flow](https://developers.klaviyo.com/en/docs/handle_your_apps_oauth_flow)
  * [Branding for app metrics](https://developers.klaviyo.com/en/docs/understanding_branded_events)
* [Build an OAuth app Academy course](https://academy.klaviyo.com/en-us/courses/build-an-oauth-app-with-klaviyo)
* [Klaviyo app development YouTube playlist](https://www.youtube.com/playlist?list=PLHkNfHgtxcUZFU_vpOM8vAKai0BC6yxzB)


