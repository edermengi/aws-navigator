import Papa, {ParseStepResult} from "papaparse";
import {Document, SimpleDocumentSearchResultSetUnit} from 'flexsearch';
import {Resource} from "./interfaces";

function newIndex() {
    return new Document({
        tokenize: 'full',
        document: {
            id: 'id',
            store: false,
            index: [
                {
                    field: "full_name"
                }
            ],
            tag: "rt_pr_rg"
        }
    });
}

let index = newIndex();
const resources: Array<Resource> = [];

function loadResources(file: File, onLoadComplete: any) {
    index = newIndex();
    let i = 0;
    let profiles = new Set<string>();
    let regions = new Set<string>();
    resources.length = 0;
    Papa.parse(file, {
        worker: true,
        step: function (row: ParseStepResult<Resource>) {
            // @ts-ignore
            const [profile, region, resourceType, resourceName] = row.data;
            if (resourceName) {
                index.add({
                    id: i,
                    full_name: `${resourceName} ${resourceType}`,
                    rt_pr_rg: [resourceType, profile, region]
                });
                profiles.add(profile);
                regions.add(region);
                resources[i++] = {rt: resourceType, rn: resourceName, rg: region, pr: profile};
            }
        },
        complete() {
            console.log("parsed:", i, "records");
            console.log("Searching 'lambda'", index.search('lambda', {index: "full_name", limit: 10}));
            onLoadComplete({
                fileName: file.name,
                totalNames: i,
                profiles: Array.from(profiles),
                regions: Array.from(regions)
            });
        }
    });
}


function searchResources(query: string): Resource[] {
    const response: SimpleDocumentSearchResultSetUnit[] =
        index.search(query, 20, {index: "full_name", limit: 20});

    if (response.length > 0) {
        const docs = response[0].result;
        console.log('Found', docs.length, 'results for ', query);
        return docs.map((id: number) => {
            // @ts-ignore
            return resources[id];
        });
    }
    return [];
}


export {
    searchResources,
    loadResources
};