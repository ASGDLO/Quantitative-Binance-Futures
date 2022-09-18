# Webhook usage

## Configuration

Enable webhooks by adding a webhook-section to your configuration file, and setting `webhook.enabled` to `true`.

Sample configuration (tested using IFTTT).

```json
  "webhook": {
        "enabled": true,
        "url": "https://maker.ifttt.com/trigger/<YOUREVENT>/with/key/<YOURKEY>/",
        "webhookbuy": {
            "value1": "Buying {pair}",
            "value2": "limit {limit:8f}",
            "value3": "{stake_amount:8f} {stake_currency}"
        },
        "webhookbuycancel": {
            "value1": "Cancelling Open Buy Order for {pair}",
            "value2": "limit {limit:8f}",
            "value3": "{stake_amount:8f} {stake_currency}"
        },
         "webhookbuyfill": {
            "value1": "Buy Order for {pair} filled",
            "value2": "at {open_rate:8f}",
            "value3": ""
        },
        "webhooksell": {
            "value1": "Selling {pair}",
            "value2": "limit {limit:8f}",
            "value3": "profit: {profit_amount:8f} {stake_currency} ({profit_ratio})"
        },
        "webhooksellcancel": {
            "value1": "Cancelling Open Sell Order for {pair}",
            "value2": "limit {limit:8f}",
            "value3": "profit: {profit_amount:8f} {stake_currency} ({profit_ratio})"
        },
        "webhooksellfill": {
            "value1": "Sell Order for {pair} filled",
            "value2": "at {close_rate:8f}.",
            "value3": ""
        },
        "webhookstatus": {
            "value1": "Status: {status}",
            "value2": "",
            "value3": ""
        }
    },
```

The url in `webhook.url` should point to the correct url for your webhook. If you're using [IFTTT](https://ifttt.com) (as shown in the sample above) please insert your event and key to the url.

You can set the POST body format to Form-Encoded (default), JSON-Encoded, or raw data. Use `"format": "form"`, `"format": "json"`, or `"format": "raw"` respectively. Example configuration for Mattermost Cloud integration:

```json
  "webhook": {
        "enabled": true,
        "url": "https://<YOURSUBDOMAIN>.cloud.mattermost.com/hooks/<YOURHOOK>",
        "format": "json",
        "webhookstatus": {
            "text": "Status: {status}"
        }
    },
```

The result would be a POST request with e.g. `{"text":"Status: running"}` body and `Content-Type: application/json` header which results `Status: running` message in the Mattermost channel.

When using the Form-Encoded or JSON-Encoded configuration you can configure any number of payload values, and both the key and value will be ouput in the POST request. However, when using the raw data format you can only configure one value and it **must** be named `"data"`. In this instance the data key will not be output in the POST request, only the value. For example:

```json
  "webhook": {
        "enabled": true,
        "url": "https://<YOURHOOKURL>",
        "format": "raw",
        "webhookstatus": {
            "data": "Status: {status}"
        }
    },
```

The result would be a POST request with e.g. `Status: running` body and `Content-Type: text/plain` header.

Optional parameters are available to enable automatic retries for webhook messages. The `webhook.retries` parameter can be set for the maximum number of retries the webhook request should attempt if it is unsuccessful (i.e. HTTP response status is not 200). By default this is set to `0` which is disabled. An additional `webhook.retry_delay` parameter can be set to specify the time in seconds between retry attempts. By default this is set to `0.1` (i.e. 100ms). Note that increasing the number of retries or retry delay may slow down the trader if there are connectivity issues with the webhook. Example configuration for retries:

```json
  "webhook": {
        "enabled": true,
        "url": "https://<YOURHOOKURL>",
        "retries": 3,
        "retry_delay": 0.2,
        "webhookstatus": {
            "status": "Status: {status}"
        }
    },
```

Different payloads can be configured for different events. Not all fields are necessary, but you should configure at least one of the dicts, otherwise the webhook will never be called.

### Webhookbuy

The fields in `webhook.webhookbuy` are filled when the bot executes a buy. Parameters are filled using string.format.
Possible parameters are:

* `trade_id`
* `exchange`
* `pair`
* ~~`limit` # Deprecated - should no longer be used.~~
* `open_rate`
* `amount`
* `open_date`
* `stake_amount`
* `stake_currency`
* `base_currency`
* `fiat_currency`
* `order_type`
* `current_rate`
* `buy_tag`

### Webhookbuycancel

The fields in `webhook.webhookbuycancel` are filled when the bot cancels a buy order. Parameters are filled using string.format.
Possible parameters are:

* `trade_id`
* `exchange`
* `pair`
* `limit`
* `amount`
* `open_date`
* `stake_amount`
* `stake_currency`
* `base_currency`
* `fiat_currency`
* `order_type`
* `current_rate`
* `buy_tag`

### Webhookbuyfill

The fields in `webhook.webhookbuyfill` are filled when the bot filled a buy order. Parameters are filled using string.format.
Possible parameters are:

* `trade_id`
* `exchange`
* `pair`
* `open_rate`
* `amount`
* `open_date`
* `stake_amount`
* `stake_currency`
* `base_currency`
* `fiat_currency`
* `order_type`
* `current_rate`
* `buy_tag`

### Webhooksell

The fields in `webhook.webhooksell` are filled when the bot sells a trade. Parameters are filled using string.format.
Possible parameters are:

* `trade_id`
* `exchange`
* `pair`
* `gain`
* `limit`
* `amount`
* `open_rate`
* `profit_amount`
* `profit_ratio`
* `stake_currency`
* `base_currency`
* `fiat_currency`
* `sell_reason`
* `order_type`
* `open_date`
* `close_date`

### Webhooksellfill

The fields in `webhook.webhooksellfill` are filled when the bot fills a sell order (closes a Trae). Parameters are filled using string.format.
Possible parameters are:

* `trade_id`
* `exchange`
* `pair`
* `gain`
* `close_rate`
* `amount`
* `open_rate`
* `current_rate`
* `profit_amount`
* `profit_ratio`
* `stake_currency`
* `base_currency`
* `fiat_currency`
* `sell_reason`
* `order_type`
* `open_date`
* `close_date`

### Webhooksellcancel

The fields in `webhook.webhooksellcancel` are filled when the bot cancels a sell order. Parameters are filled using string.format.
Possible parameters are:

* `trade_id`
* `exchange`
* `pair`
* `gain`
* `limit`
* `amount`
* `open_rate`
* `current_rate`
* `profit_amount`
* `profit_ratio`
* `stake_currency`
* `base_currency`
* `fiat_currency`
* `sell_reason`
* `order_type`
* `open_date`
* `close_date`

### Webhookstatus

The fields in `webhook.webhookstatus` are used for regular status messages (Started / Stopped / ...). Parameters are filled using string.format.

The only possible value here is `{status}`.
