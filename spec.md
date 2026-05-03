# Specification

I want to create an application that helps me with creating auctions for selling old stuff on ebay.de or kleinanzeigen.de (it would be nice if the implementation is provider-agnostic, so that other providers could be plugged later). 

The application should streamline the process from taking the photos of the item, describing them, evaluate the condition, check prices based on the condition and finally creating the auction or posting on the provider.

Here are the steps the application is supposed to do:

1. I put photos into a folder and give access to it to the application
2. The application is analyzing the photos and check which belong to the same item 
3. The application enhances the photos (lighting, aspect ratio, view port)
4. Based on the photos the application identifies the items and searches online for reasonable prices based on the item's condition
5. When all information are available, the application creates as Markdown with a summary for each item containing the designated sales prices and the enhanced photos. The file is for inspection before the auction/listing is created
6. When the user approves the item listing, it is posted on kleinanzeigen.de or ebay (user can choose)
    - Prefer using an API to create the posting, but fallback to playwright MCP when now API is available

You can choose between Kotlin, Python, Rust and Go for programming languages. Whatever seems most suitable for the job. For analyzing and search online you can use Claude (or some other LLMs later), but put as much logic in the code as possible to save tokens.