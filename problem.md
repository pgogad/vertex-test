# AI Product Engineer Real Case - Selecting Search Results

You are an AI Product Engineer working on Vertex's Smart Categorization application, specifically the Smart Categorization Engine. To use Vertex’s O-Series, which calculates tax obligations for customers, customers must first map all their products to Vertex’s Tax Categories in order for O-Series to be able to calculate tax obligations for their products correctly. For customers that have 100k+ products, like big box retailers, this is a very tedious and time-consuming process that is a burden on tax departments.

Vertex's Smart Categorization is an application where clients upload a CSV file containing product data (including product title and description). The application then automatically categorizes each product into a Vertex Tax Category and presents the results to the user.

In this scenario, you are responsible for the performance of the Smart Categorization Engine, which handles the product categorization pipeline. After beginning the project, you analyze Vertex Tax Categories and discover that many categories require specific understanding of product components for accurate categorization.

For example, to categorize a beverage accurately it is important to know its ingredient list because the following dimensions change how the beverage would be categorized: Carbonation, Juice %, Sweetener, Flavoring, Alcohol, Kefir, Milk, and more.

After gathering as much ‘real’ available client data you learn that Tax Departments rarely have this level of detail on the products they must categorize, they almost always only have product titles and product descriptions, and the Tax Department resources end up doing web searches to find necessary details.

You have determined that in order to be able to accurately categorize uploaded products, you must build an ‘enrichment pipeline’ which takes in uploaded product information (product title and product description), performs a web search, selects results that are for the product that was uploaded, and then pulls information from those websites to create an ‘enriched product description’, which will be used downstream for classification to Vertex Tax Categories.

This case is focused on the step where the pipeline must determine which search results are for the uploaded product. You must build a GenAI pipeline that is able to ingest: product title, product description, and search results, and output which of the search results are for the given product title and product description.

This is a critical step, because these selected sites will be presented to the user in the application, and the content of the websites will be pulled to find additional details about the product. If the websites are for false information the customers will not trust the system, and our classifications will be wrong because we use incorrect product data.

Attached in this email is a json with 50 products (`product_title` and `product_description`), search results for those products, and what a team has determined to be ‘trustworthy’ results for each of those products. These are the links they would have clicked on to find more information about the product.

There is a lot of ‘grey’ area in this problem, different team members might have different heuristics on what to trust. Size of product does not affect categorization, so different sizes are ignored because the components will be the same. Some products may not be linked to a search result, for instance a product with title ‘Apple’ would not be linked to results, because it is general item and not linked to a specific branded product. Variations like ‘flavor’ will have an impact on components (the ingredients, which affect taxability) and must be taken into account.

## Case

1. Email [ryan.gleeson@vertexinc.com](mailto:ryan.gleeson@vertexinc.com) with subject ‘AI Product Engineer Case – OpenAI Access’ once you have generated keys and tested access.

2. Create a pipeline in whatever language you are comfortable with that can ingest the json keys (`product_title`, `product_description`, `search_results`) and output the key `llm_trusted_search_results` in the same format as the provided json.

3. Select a metric(s) that you think is important to optimize for in the context of this business problem. Output those metrics when running your pipeline on data. We will run the pipeline on a new ‘test’ set in the second interview.

4. Present your approach, thinking, and next steps to give confidence that our pipeline will be production ready.

As an AI Product Engineer, you are ultimately responsible for this step of the Smart Categorization Engine performing well when in production and serving hundreds of diverse clients. Think through what that would mean operationally and what you must make clear to your business stakeholders in terms of trade-offs and requirements. You are the technical, product, and business ‘owner’ of this problem. A good lens to look at this is you are a solo founder who is creating company that solves this one problem. There is nothing out of scope for you to make this work.

Also email [ryan.gleeson@vertexinc.com](mailto:ryan.gleeson@vertexinc.com) ‘AI Product Engineer Case’ with any clarifying questions you may have. Think of what you would ask if you were a Vertex AI Product Engineer.

## Interview Format

You will be acting as a real AI Product Engineer would, presenting an update on your progress on this problem to the team.

At the beginning of the meeting, I will provide email you the new ‘test’ json file which will run in the background as you walk through your presentation. It will be the same structure as the provided json, but with different data. Make sure you can iterate through jsons of that structure and output results of your desired metric(s).

Please present you methodology, process, learnings, and anything you believe would be relevant to the product and business team. Also include what the roadmap might look like for this step.

We will then do a review of your actual code to get a deeper understanding of approach.
